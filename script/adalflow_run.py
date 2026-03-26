from adalflow import Agent, Runner
from adalflow.components.model_client.openai_client import OpenAIClient
from adalflow.core.types import (
    ToolCallActivityRunItem,
    RunItemStreamEvent,
    ToolCallRunItem,
    ToolOutputRunItem,
    FinalOutputItem
)
import asyncio
import os
import warnings
from dotenv import load_dotenv

# 过滤 requests 依赖库版本不匹配的警告
warnings.filterwarnings('ignore', message='urllib3.*doesn\'t match a supported version')

# 加载环境变量
load_dotenv()

# 从环境变量读取配置
LLM_API_KEY = os.getenv('LLM_API_KEY', 'sk-vllm')
LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'http://192.168.110.126:18001/v1')
LLM_MODEL_NAME = os.getenv('LLM_MODEL_NAME', 'Qwen/Qwen3-Coder-30B-A3B-Instruct')

# Define tools
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression."""
    try:
        result = eval(expression)
        return f"The result of {expression} is {result}"
    except Exception as e:
        return f"Error: {e}"

async def web_search(query: str="what is the weather in SF today?") -> str:
    """Web search on query."""
    await asyncio.sleep(0.5)
    return "San Francisco will be mostly cloudy today with some afternoon sun, reaching about 67 °F (20 °C)."

def counter(limit: int):
    """A counter that counts up to a limit."""
    final_output = []
    for i in range(1, limit + 1):
        stream_item = f"Count: {i}/{limit}"
        final_output.append(stream_item)
        yield ToolCallActivityRunItem(data=stream_item)
    yield final_output

# Create agent with tools
agent = Agent(
    name="MyAgent",
    tools=[calculator, web_search, counter],
    model_client=OpenAIClient(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL
    ),
    model_kwargs={"model": LLM_MODEL_NAME, "temperature": 0.3},
    max_steps=5
)

runner = Runner(agent=agent)


# Sync call - returns RunnerResult with complete execution history
result = runner.call(
    prompt_kwargs={"input_str": "Calculate 15 * 7 + 23 and count to 5"}
)

print(result.answer)
# Output: The result of 15 * 7 + 23 is 128. The counter counted up to 5: 1, 2, 3, 4, 5.

# Access step history
for step in result.step_history:
    if step.function is not None:
        print(f"Step {step.step}: {step.function.name} -> {step.observation}")
    else:
        print(f"Step {step.step}: [No function] -> {step.observation}")
# Output:
# Step 0: calculator -> The result of 15 * 7 + 23 is 128
# Step 1: counter -> ['Count: 1/5', 'Count: 2/5', 'Count: 3/5', 'Count: 4/5', 'Count: 5/5']