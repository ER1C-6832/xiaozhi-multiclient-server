-- Bound voice-turn latency for the default ChatGLM OpenAI-compatible provider.
UPDATE `ai_model_provider`
SET `fields` = '[{"key":"model_name","label":"模型名称","type":"string"},{"key":"url","label":"服务地址","type":"string"},{"key":"api_key","label":"API密钥","type":"string"},{"key":"timeout","label":"请求超时（秒）","type":"number","default":15},{"key":"max_retries","label":"失败重试次数","type":"number","default":0}]'
WHERE `id` = 'SYSTEM_LLM_chatglm';

UPDATE `ai_model_config`
SET `config_json` = JSON_SET(`config_json`, '$.timeout', 15, '$.max_retries', 0),
    `remark` = 'ChatGLM 配置：填写有效 API Key。语音请求默认 15 秒超时且不自动重试；认证失败可快速返回。'
WHERE `id` = 'LLM_ChatGLMLLM';
