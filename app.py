import json
import random
import asyncio
import aiohttp
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

app = FastAPI()

def generate_random_ip():
    return f"{random.randint(1, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(0, 255)}"

def generate_user_agent():
    os_list = ['Windows NT 10.0', 'Windows NT 6.1', 'Mac OS X 10_15_7', 'Ubuntu', 'Linux x86_64']
    browser_list = ['Chrome', 'Firefox', 'Safari', 'Edge']
    chrome_version = f"{random.randint(70, 126)}.0.{random.randint(1000, 9999)}.{random.randint(100, 999)}"
    firefox_version = f"{random.randint(70, 100)}.0"
    safari_version = f"{random.randint(600, 615)}.{random.randint(1, 9)}.{random.randint(1, 9)}"
    edge_version = f"{random.randint(80, 100)}.0.{random.randint(1000, 9999)}.{random.randint(100, 999)}"

    os = random.choice(os_list)
    browser = random.choice(browser_list)

    if browser == 'Chrome':
        return f"Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
    elif browser == 'Firefox':
        return f"Mozilla/5.0 ({os}; rv:{firefox_version}) Gecko/20100101 Firefox/{firefox_version}"
    elif browser == 'Safari':
        return f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/{safari_version} (KHTML, like Gecko) Version/{safari_version.split('.')[0]}.1.2 Safari/{safari_version}"
    elif browser == 'Edge':
        return f"Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{edge_version} Safari/537.36 Edg/{edge_version}"

def format_openai_response(content, finish_reason=None):
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion.chunk",
        "created": 1677652288,
        "model": "gpt-4o",
        "choices": [{
            "delta": {"content": content} if content else {"finish_reason": finish_reason},
            "index": 0,
            "finish_reason": finish_reason
        }]
    }

async def parse_sse(response):
    """
    Parses Server-Sent Events from an aiohttp response.
    """
    event = {}
    async for line in response.content:
        line = line.decode('utf-8').strip()
        if not line:
            # Empty line indicates end of event
            if event:
                yield event
                event = {}
            continue
        if line.startswith(':'):
            # Comment line, ignore
            continue
        if ':' in line:
            key, value = line.split(':', 1)
            value = value.lstrip()
        else:
            key = line
            value = ''
        if key in event:
            event[key] += '\n' + value
        else:
            event[key] = value
    if event:
        yield event

@app.post('/v1/chat/completions')
async def chat_completions(request: Request):
    data = await request.json()
    messages = data.get('messages', [])
    stream = data.get('stream', False)
    
    if not messages:
        return {"error": "No messages provided"}, 400
    
    model = data.get('model', 'gpt-4o')
    if model.startswith('gpt') or model.startswith('o1'):
        endpoint = "openAI"
        original_api_url = 'https://chatpro.ai-pro.org/api/ask/openAI'
    elif model.startswith('claude'):
        endpoint = "claude"
        original_api_url = 'https://chatpro.ai-pro.org/api/ask/claude'
    elif model.startswith('gemini'):
        endpoint = "gemini"
        original_api_url = 'https://chatpro.ai-pro.org/api/ask/gemini' 
    else:
        return {"error": "Unsupported model"}, 400

    headers = {
        'content-type': 'application/json',
        'X-Forwarded-For': generate_random_ip(),
        'origin': 'https://chatpro.ai-pro.org',
        'user-agent': generate_user_agent()
    }

    async def generate():
        full_response = ""
        while True:
            conversation = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])
            conversation += "\nPlease follow and reply to the user’s recent messages and avoid answers that summarize the conversation history."
            
            payload = {
                "text": conversation,
                "endpoint": endpoint,
                "model": model
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(original_api_url, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        yield f"data: {json.dumps({'error': 'Failed to connect to upstream server'})}\n\n"
                        return

                    async for event in parse_sse(resp):
                        if 'data' in event:
                            data = event['data']
                            if data.startswith('{"text":'):
                                data_json = json.loads(data)
                                new_content = data_json['text'][len(full_response):]
                                full_response = data_json['text']
                                if new_content:
                                    yield f"data: {json.dumps(format_openai_response(new_content))}\n\n"
                            elif '"final":true' in data:
                                final_data = json.loads(data)
                                response_message = final_data.get('responseMessage', {})
                                finish_reason = response_message.get('finish_reason', 'stop')
                                if finish_reason == 'length':
                                    messages.append({"role": "assistant", "content": full_response})
                                    messages.append({"role": "user", "content": "Please continue your output and do not repeat the previous content"})
                                    break  # Continue with the next request
                                else:
                                    last_content = response_message.get('text', '')
                                    if last_content and last_content != full_response:
                                        yield f"data: {json.dumps(format_openai_response(last_content[len(full_response):]))}\n\n"
                                    yield f"data: {json.dumps(format_openai_response('', finish_reason))}\n\n"
                                    yield "data: [DONE]\n\n"
                                    return
            yield f"data: {json.dumps(format_openai_response('', 'stop'))}\n\n"
            yield "data: [DONE]\n\n"

    if stream:
        return StreamingResponse(generate(), media_type='text/event-stream')
    else:
        full_response = ""
        finish_reason = "stop"
        async for chunk in generate():
            if chunk.startswith("data: ") and not chunk.strip() == "data: [DONE]":
                response_data = json.loads(chunk[6:])
                if 'choices' in response_data and response_data['choices']:
                    delta = response_data['choices'][0].get('delta', {})
                    if 'content' in delta:
                        full_response += delta['content']
                    if 'finish_reason' in delta:
                        finish_reason = delta['finish_reason']

        return {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1677652288,
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_response
                },
                "finish_reason": finish_reason
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }

@app.get("/v1/models")
async def get_models():
    """Returns a list of available models."""
    return {
        "object": "list",
        "data": [
            {"id": "o1-preview", "name": "o1-preview"},
            {"id": "o1-mini", "name": "o1-mini"},
            {"id": "gpt-4o", "name": "gpt-4o"},
            {"id": "gpt-4o-mini", "name": "gpt-4o-mini"},
            {"id": "claude-3.5-sonnet", "name": "claude-3.5-sonnet"},
            {"id": "claude-3.5-haiku", "name": "claude-3.5-haiku"},
            {"id": "claude-3-opus", "name": "claude-3-opus"},
            {"id": "claude-3-sonnet", "name": "claude-3-sonnet"},
            {"id": "claude-3-haiku", "name": "claude-3-haiku"},
            {"id": "gemini-exp-1121", "name": "gemini-exp-1121"},  
            {"id": "gemini-exp-1114", "name": "gemini-exp-1114"},
            {"id": "gemini-1.5-pro", "name": "gemini-1.5-pro"},
            {"id": "gemini-1.5-pro-latest", "name": "gemini-1.5-pro-latest"},
            {"id": "gemini-1.5-flash", "name": "gemini-1.5-flash"},
            {"id": "gemini-1.5-flash-latest", "name": "gemini-1.5-flash-latest"},
            {"id": "gemini-pro", "name": "gemini-pro"}
        ]
    }
@app.get("/")
async def root():
    return {"status": "ok"}
