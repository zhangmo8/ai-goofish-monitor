import asyncio
import json
import os
import sys
from typing import Awaitable, Callable, Optional

import aiofiles

from src.infrastructure.external.ai_client import AIClient

# The meta-prompt to instruct the AI
META_PROMPT_TEMPLATE = """
你是一位世界级的AI提示词工程大师。你的任务是根据用户提供的【购买需求】，模仿一个【参考范例】，为闲鱼监控机器人的AI分析模块（代号 EagleEye）生成一份全新的【分析标准】文本。

你的输出必须严格遵循【参考范例】的结构、语气和核心原则，但内容要完全针对用户的【购买需求】进行定制。最终生成的文本将作为AI分析模块的思考指南。

---
这是【参考范例】（`macbook_criteria.txt`）：
```text
{reference_text}
```
---

这是用户的【购买需求】：
```text
{user_description}
```
---

请现在开始生成全新的【分析标准】文本。请注意：
1.  **只输出新生成的文本内容**，不要包含任何额外的解释、标题或代码块标记。
2.  保留范例中的 `[V6.3 核心升级]`、`[V6.4 逻辑修正]` 等版本标记，这有助于保持格式一致性。
3.  将范例中所有与 "MacBook" 相关的内容，替换为与用户需求商品相关的内容。
4.  思考并生成针对新商品类型的“一票否决硬性原则”和“危险信号清单”。
"""

ProgressCallback = Callable[[str, str], Awaitable[None]]
RETRYABLE_AI_ERROR_MARKERS = (
    "internalserviceerror",
    "internal server error",
    "service unavailable",
    "temporarily unavailable",
    "timeout",
    "timed out",
    "connection",
    "rate limit",
)


async def _report_progress(
    progress_callback: Optional[ProgressCallback],
    step_key: str,
    message: str,
) -> None:
    if progress_callback:
        await progress_callback(step_key, message)


def _is_retryable_ai_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code >= 500 or status_code == 429

    message = str(exc).lower()
    return any(marker in message for marker in RETRYABLE_AI_ERROR_MARKERS)


def _format_ai_error(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return f"HTTP {status_code}: {exc}"
    return str(exc)


async def generate_criteria(
    user_description: str,
    reference_file_path: str,
    progress_callback: Optional[ProgressCallback] = None,
) -> str:
    """
    Generates a new criteria file content using AI.
    """
    ai_client = AIClient()
    if not ai_client.is_available():
        ai_client.refresh()
    if not ai_client.is_available():
        raise RuntimeError("AI客户端未初始化，无法生成分析标准。请检查.env配置。")

    await _report_progress(progress_callback, "reference", "正在读取参考文件。")
    print(f"正在读取参考文件: {reference_file_path}")
    try:
        with open(reference_file_path, 'r', encoding='utf-8') as f:
            reference_text = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"参考文件未找到: {reference_file_path}")
    except IOError as e:
        raise IOError(f"读取参考文件失败: {e}")

    await _report_progress(progress_callback, "prompt", "正在构建发送给 AI 的指令。")
    print("正在构建发送给AI的指令...")
    prompt = META_PROMPT_TEMPLATE.format(
        reference_text=reference_text,
        user_description=user_description
    )

    await _report_progress(progress_callback, "llm", "正在调用 AI 生成分析标准。")
    print("正在调用AI生成新的分析标准，请稍候...")
    request_params = {
        "model": ai_client.settings.model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5,
    }
    if ai_client.settings.enable_thinking:
        request_params["extra_body"] = {"enable_thinking": False}

    max_attempts = 3
    last_error: Optional[Exception] = None

    for attempt in range(max_attempts):
        try:
            response = await ai_client.client.chat.completions.create(**request_params)
            if hasattr(response, 'choices'):
                generated_text = response.choices[0].message.content
            else:
                generated_text = response

            print("AI已成功生成内容。")
            if generated_text is None or generated_text.strip() == "":
                raise RuntimeError("AI返回的内容为空，请检查模型配置或重试。")

            return generated_text.strip()
        except Exception as exc:
            last_error = exc
            error_text = _format_ai_error(exc)
            retryable = _is_retryable_ai_error(exc)
            is_last_attempt = attempt == max_attempts - 1
            print(f"调用 OpenAI API 时出错，第 {attempt + 1}/{max_attempts} 次尝试失败: {error_text}")

            if retryable and not is_last_attempt:
                wait_seconds = attempt + 2
                retry_message = f"AI 服务暂时异常，{wait_seconds} 秒后进行第 {attempt + 2} 次重试。"
                await _report_progress(progress_callback, "llm", retry_message)
                await asyncio.sleep(wait_seconds)
                continue

            if retryable:
                raise RuntimeError(
                    f"AI 服务暂时不可用，已重试 {max_attempts} 次仍失败。最后一次错误: {error_text}"
                ) from exc

            raise RuntimeError(f"调用 AI 生成分析标准失败: {error_text}") from exc

    raise RuntimeError(f"调用 AI 生成分析标准失败: {_format_ai_error(last_error)}")


async def update_config_with_new_task(new_task: dict, config_file: str = "config.json"):
    """
    将一个新任务添加到指定的JSON配置文件中。
    """
    print(f"正在更新配置文件: {config_file}")
    try:
        # 读取现有配置
        config_data = []
        if os.path.exists(config_file):
            async with aiofiles.open(config_file, 'r', encoding='utf-8') as f:
                content = await f.read()
                # 处理空文件的情况
                if content.strip():
                    try:
                        config_data = json.loads(content)
                        print(f"成功读取现有配置，当前任务数量: {len(config_data)}")
                    except json.JSONDecodeError as e:
                        print(f"解析配置文件失败，将创建新配置: {e}")
                        config_data = []
        else:
            print(f"配置文件不存在，将创建新文件: {config_file}")

        # 追加新任务
        config_data.append(new_task)

        # 写回配置文件
        async with aiofiles.open(config_file, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(config_data, ensure_ascii=False, indent=2))
            print(f"配置文件写入完成")

        print(f"成功！新任务 '{new_task.get('task_name')}' 已添加到 {config_file} 并已启用。")
        return True
    except json.JSONDecodeError as e:
        error_msg = f"错误: 配置文件 {config_file} 格式错误，无法解析: {e}"
        sys.stderr.write(error_msg + "\n")
        print(error_msg)
        return False
    except IOError as e:
        error_msg = f"错误: 读写配置文件失败: {e}"
        sys.stderr.write(error_msg + "\n")
        print(error_msg)
        return False
    except Exception as e:
        error_msg = f"错误: 更新配置文件时发生未知错误: {e}"
        sys.stderr.write(error_msg + "\n")
        print(error_msg)
        import traceback
        print(traceback.format_exc())
        return False
