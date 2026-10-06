import os
import sys
import logging
from openai import OpenAI

logging.basicConfig(format='[%(asctime)s %(levelname)s] %(message)s',
                    datefmt='%m/%d/%Y %H:%M:%S',
                    level=logging.INFO)


class OpenAIClient:
    """OpenAI 兼容的 chat/completions 客户端。

    只要服务端提供 /chat/completions（OpenAI 规范），本客户端都能用：
      - OpenAI 官方：base_url = https://api.openai.com
      - DeepSeek 官方：base_url = https://api.deepseek.com，model = deepseek-chat
      - Moonshot / 智谱 / 硅基流动 / vLLM / Ollama 等同样适用

    端点与模型按「构造参数 > 环境变量 CRS_BASE_URL / CRS_MODEL > 默认值」的优先级取。
    """

    def __init__(self, api_key, base_url=None, model_name=None):
        """
        Initialize OpenAI client
        @param api_key: API key
        @param base_url: Base URL for API endpoint (默认取 CRS_BASE_URL)
        @param model_name: Model to use (默认取 CRS_MODEL)
        """
        self.model_name = model_name or os.environ.get("CRS_MODEL") or "claude-sonnet-4-5-20250929"
        base_url = base_url or os.environ.get("CRS_BASE_URL") or "https://api.openai.com"
        self.base_url = base_url

        # 可选：限制最大输出长度（某些服务默认 4096，分析结果较长时可能被截断）
        self.max_tokens = os.environ.get("CRS_MAX_TOKENS")

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=120.0,
            max_retries=3
        )
        logging.info(f"OpenAI-compatible client initialized: base_url={base_url}, model={self.model_name}")

    def send_message(self, prompt_content):
        """
        Send a message to OpenAI API
        @param prompt_content: The prompt/message to send
        @return: Response text
        """
        try:
            kwargs = {
                "model": self.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt_content,
                    },
                ],
            }
            if self.max_tokens:
                kwargs["max_tokens"] = int(self.max_tokens)

            response = self.client.chat.completions.create(**kwargs)

            return response.choices[0].message.content

        except Exception as e:
            logging.error(f"OpenAI API error: {e}")
            raise


if __name__ == '__main__':
    """Test OpenAI API client"""

    # Check if API key file exists
    api_key = os.environ.get('ANTHROPIC_AUTH_TOKEN')
    try:
        client = OpenAIClient(api_key)
    except Exception as e:
        print(f"✗ Failed to initialize client: {e}")
        sys.exit(1)

    # Test API call
    print("\nTesting API connection...")
    test_prompt = "Hello! Please respond with 'API works!' to confirm the connection."

    try:
        response = client.send_message(test_prompt)
        print(f"✓ API Response:\n 🤖:{response}")
        print("\n✓ OpenAI API test successful!")

    except Exception as e:
        print(f"✗ API call failed: {e}")
        sys.exit(1)