"""官方 OpenAI 兼容接口预设，地址和模型仍可手动编辑。"""
PRESETS = [
    ('自定义接口', '', '', '填写服务商提供的 OpenAI 兼容地址和模型名称。'),
    ('DeepSeek', 'https://api.deepseek.com', 'deepseek-flash', '使用 DeepSeek 平台 API Key。'),
    ('千问 · 北京', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen-plus',
     '使用北京地域的百炼 API Key；也可将地址替换为工作空间专属域名。图片题请改用有权限的视觉模型。'),
    ('千问 · 国际', 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1', 'qwen-plus',
     '使用新加坡地域的百炼 API Key；API Key 须与接口地域一致。'),
    ('Gemini', 'https://generativelanguage.googleapis.com/v1beta/openai', 'gemini-3.8-flash',
     '使用 Google AI Studio API Key；需要本机网络能够访问 Google 接口。'),
]
