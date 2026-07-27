-- Promote the validated Sherpa-ONNX SenseVoice integration to the B2 branch default.
-- Keep ASR_FunASR enabled as an explicit rollback option.

UPDATE `ai_model_provider`
SET `name` = 'Sherpa-ONNX SenseVoice（本地增强）',
    `sort` = 1,
    `fields` = '[{"key":"model_dir","label":"模型目录","type":"string"},{"key":"model_filename","label":"SenseVoice 模型文件","type":"string","default":"model.onnx"},{"key":"tokens_filename","label":"Tokens 文件","type":"string","default":"tokens.txt"},{"key":"vad_model_path","label":"Silero VAD 模型","type":"string","default":"silero_vad.onnx"},{"key":"output_dir","label":"输出目录","type":"string","default":"tmp/"},{"key":"language","label":"识别语言","type":"string","default":"zh"},{"key":"num_threads","label":"推理线程","type":"number","default":4},{"key":"vad_enabled","label":"启用句内边界精修","type":"boolean","default":true},{"key":"vad_min_silence_duration","label":"尾部静音秒数","type":"number","default":0.5},{"key":"vad_buffer_size_sec","label":"VAD 缓冲秒数","type":"number","default":30},{"key":"vad_pre_roll_ms","label":"前补偿毫秒","type":"number","default":320},{"key":"vad_post_roll_ms","label":"后补偿毫秒","type":"number","default":240},{"key":"min_segment_sec","label":"最短片段秒数","type":"number","default":0.3},{"key":"fallback_to_full_audio","label":"VAD 未命中时回退整句","type":"boolean","default":true},{"key":"audio_level_diagnostics","label":"记录音量诊断","type":"boolean","default":true}]',
    `update_date` = NOW(),
    `updater` = 1
WHERE `id` = 'SYSTEM_ASR_SherpaSenseVoiceB2';

UPDATE `ai_model_provider`
SET `sort` = 2
WHERE `id` = 'SYSTEM_ASR_FunASR';

UPDATE `ai_model_config`
SET `is_default` = 0
WHERE `model_type` = 'ASR';

UPDATE `ai_model_config`
SET `model_name` = 'Sherpa-ONNX SenseVoice（本地增强）',
    `is_default` = 1,
    `is_enabled` = 1,
    `sort` = 1,
    `doc_link` = 'https://github.com/ER1C-6832/xiaozhi-multiclient-server/blob/platform/main/xiaozhi-server/docs/sherpa-sensevoice-b2.md',
    `remark` = '默认本地 ASR：Sherpa-ONNX + SenseVoice FP32，使用逐句独立的 Silero VAD 做句内边界精修。模型目录必须包含 model.onnx、tokens.txt 和 silero_vad.onnx；FunASR 保留为回滚选项。',
    `update_date` = NOW(),
    `updater` = 1
WHERE `id` = 'ASR_SherpaSenseVoiceB2';

UPDATE `ai_model_config`
SET `sort` = 2
WHERE `id` = 'ASR_FunASR';

UPDATE `ai_agent_template`
SET `asr_model_id` = 'ASR_SherpaSenseVoiceB2',
    `updated_at` = NOW(),
    `updater` = 1
WHERE `asr_model_id` = 'ASR_FunASR';
