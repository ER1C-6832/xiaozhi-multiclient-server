-- Optional Sherpa-ONNX SenseVoice provider with utterance-local VAD refinement.
DELETE FROM `ai_model_provider` WHERE `id` = 'SYSTEM_ASR_SherpaSenseVoiceB2';
INSERT INTO `ai_model_provider`
(`id`, `model_type`, `provider_code`, `name`, `fields`, `sort`, `creator`, `create_date`, `updater`, `update_date`)
VALUES
('SYSTEM_ASR_SherpaSenseVoiceB2', 'ASR', 'sherpa_sensevoice_b2', 'Sherpa SenseVoice B2（本地边界精修）',
 '[{"key":"model_dir","label":"模型目录","type":"string"},{"key":"model_filename","label":"模型文件名","type":"string","default":"model.onnx"},{"key":"tokens_filename","label":"Tokens 文件名","type":"string","default":"tokens.txt"},{"key":"vad_model_path","label":"Silero VAD 文件","type":"string","default":"silero_vad.onnx"},{"key":"output_dir","label":"输出目录","type":"string","default":"tmp/"},{"key":"language","label":"识别语言","type":"string","default":"zh"},{"key":"num_threads","label":"推理线程","type":"number","default":4},{"key":"vad_enabled","label":"启用句内边界精修","type":"boolean","default":true},{"key":"vad_min_silence_duration","label":"尾部静音秒数","type":"number","default":0.5},{"key":"vad_buffer_size_sec","label":"VAD 缓冲秒数","type":"number","default":30},{"key":"vad_pre_roll_ms","label":"前补偿毫秒","type":"number","default":320},{"key":"vad_post_roll_ms","label":"后补偿毫秒","type":"number","default":240},{"key":"min_segment_sec","label":"最短片段秒数","type":"number","default":0.3},{"key":"fallback_to_full_audio","label":"VAD 未命中时回退整句","type":"boolean","default":true},{"key":"audio_level_diagnostics","label":"记录音量诊断","type":"boolean","default":true}]',
 11, 1, NOW(), 1, NOW());

DELETE FROM `ai_model_config` WHERE `id` = 'ASR_SherpaSenseVoiceB2';
INSERT INTO `ai_model_config`
(`id`, `model_type`, `model_code`, `model_name`, `is_default`, `is_enabled`, `config_json`, `doc_link`, `remark`, `sort`, `creator`, `create_date`, `updater`, `update_date`)
VALUES
('ASR_SherpaSenseVoiceB2', 'ASR', 'SherpaSenseVoiceB2', 'Sherpa SenseVoice B2', 0, 1,
 '{"type":"sherpa_sensevoice_b2","model_dir":"models/sherpa-sensevoice-b2","model_filename":"model.onnx","tokens_filename":"tokens.txt","vad_model_path":"silero_vad.onnx","output_dir":"tmp/","language":"zh","num_threads":4,"vad_enabled":true,"vad_min_silence_duration":0.5,"vad_buffer_size_sec":30,"vad_pre_roll_ms":320,"vad_post_roll_ms":240,"min_segment_sec":0.3,"fallback_to_full_audio":true,"audio_level_diagnostics":true}',
 NULL,
 '可选本地 ASR：Sherpa-ONNX + SenseVoice FP32 + 句内 Silero 边界精修。模型目录需包含 model.onnx、tokens.txt、silero_vad.onnx；切回 FunASR 即可回滚。',
 11, 1, NOW(), 1, NOW());
