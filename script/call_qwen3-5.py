"""
Qwen3.5-Plus API 调用示例脚本
"""
import requests
import json


def call_qwen_api(messages, model="qwen3.5-plus", temperature=0.7, max_tokens=2048):
    """
    调用 Qwen3.5-Plus 模型 API
    
    Args:
        messages: 对话消息列表，格式为 [{"role": "user/assistant", "content": "..."}]
        model: 模型名称，默认 qwen3.5-plus
        temperature: 温度参数，控制随机性 (0-1)
        max_tokens: 最大生成 token 数
    
    Returns:
        API 响应结果
    """
    # API 配置
    api_url = "http://192.168.1.246:3000/v1/chat/completions"
    api_key = "sk-OC5Y16Hcm6FTkd4TF1B891Ec53Be4940AdF2Bb15C8Ad90Ca"
    
    # 请求头
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    # 请求体
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "enable_thinking": False  # 关闭 thinking 模式
    }
    
    try:
        # 发送请求
        response = requests.post(api_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        
        # 解析响应
        result = response.json()
        return result
    
    except requests.exceptions.RequestException as e:
        print(f"请求失败: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"错误响应: {e.response.text}")
        return None


def simple_chat():
    """测试简单对话"""
    print("=" * 60)
    print("测试1: 简单对话")
    print("=" * 60)
    
    messages = [
        {"role": "user", "content": "你好，请介绍一下你自己"}
    ]
    
    result = call_qwen_api(messages)
    
    if result:
        print("\n完整响应:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
        # 提取回复内容
        if "choices" in result and len(result["choices"]) > 0:
            reply = result["choices"][0]["message"]["content"]
            print("\nAI 回复:")
            print(reply)
    else:
        print("未获取到有效响应")


def multi_turn_chat():
    """测试多轮对话"""
    print("\n" + "=" * 60)
    print("测试2: 多轮对话")
    print("=" * 60)
    
    messages = [
        {"role": "user", "content": "Python 有哪些优点?"},
        {"role": "assistant", "content": "Python 有很多优点，包括：1. 语法简洁易读 2. 丰富的第三方库 3. 跨平台支持 4. 强大的社区支持"},
        {"role": "user", "content": "能详细说说第三个优点吗？"}
    ]
    
    result = call_qwen_api(messages)
    
    if result:
        if "choices" in result and len(result["choices"]) > 0:
            reply = result["choices"][0]["message"]["content"]
            print("\nAI 回复:")
            print(reply)


def streaming_chat():
    """测试流式输出（需要修改 API 调用方式）"""
    print("\n" + "=" * 60)
    print("测试3: 流式输出示例（代码参考）")
    print("=" * 60)
    print("""
# 流式输出示例代码：
import requests

api_url = "http://192.168.1.246:3000/v1/chat/completions"
api_key = "sk-OC5Y16Hcm6FTkd4TF1B891Ec53Be4940AdF2Bb15C8Ad90Ca"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}"
}

payload = {
    "model": "qwen3.5-plus",
    "messages": [{"role": "user", "content": "写一首诗"}],
    "stream": True  # 开启流式输出
}

response = requests.post(api_url, headers=headers, json=payload, stream=True)

for line in response.iter_lines():
    if line:
        line_str = line.decode('utf-8')
        if line_str.startswith('data: '):
            data = line_str[6:]
            if data != '[DONE]':
                chunk = json.loads(data)
                if chunk['choices'][0]['delta'].get('content'):
                    print(chunk['choices'][0]['delta']['content'], end='', flush=True)
    """)


if __name__ == "__main__":
    print("开始测试 Qwen3.5-Plus API 调用\n")
    
    # 运行测试
    simple_chat()
    multi_turn_chat()
    streaming_chat()
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
