# 系统音频问答

Windows 桌面软件。读取选定扬声器或耳机的系统回放，触发后转写最近一段声音，结合简历与项目经历生成面试回答。

窗口采用暖灰表面、深色正文和低饱和强调色。Windows 11 标题栏沿用同色，问答页可切换紧凑窗口。功能规格见 [设计说明](DESIGN_SPEC.md)，验收流程见 [测试方案](TEST_PLAN.md)，最近实现记录见 [实现日志](IMPLEMENTATION_LOG.md)。会前与问答布局原型见 [UI 原型](ui-design/index.html)。

## 使用

1. 启动程序后会优先选用上次选择且已配置密钥的服务；首次启动时自动选择可用服务。在「设置」填写 API Key 并点「保存密钥」，密钥使用当前 Windows 用户的 DPAPI 加密保存。DeepSeek 和 OpenAI 在按键提交时转写最近系统音频；百炼配置业务空间后提供实时字幕。
2. 到「面试准备」填公司、岗位和轮次，可补岗位描述与关注点，再导入或粘贴简历及项目材料。支持 TXT、MD、DOCX、文字版 PDF，多份合计最多 48,000 字符。默认中文，可选其他语言。点「开始本场」锁定面试信息和资料版本。之后修改的是草稿，直到用它开启新场。「保存预设」可复用面试字段，简历由每场选用。
3. 点击「开始监听」。DeepSeek 或 OpenAI 模式下，问题播完时按 **Shift+Z** 或点击「回答这题」，软件会转写最近缓冲的系统音频并回答。百炼实时字幕连接后，字幕持续显示但不会自动答题，按键或点「回答这题」才提交。默认回听 25 秒，可选 10、15、25 或 40 秒。
4. 转写有错可以改字后点「用当前转写重答」，也可切换快速或深度再重答。重答复用文本。「查看参考资料」显示本题发给模型的资料片段。用「记忆与反馈」保留纠正和表达偏好，选择仅本场或存入本地记忆。

### 开场检查与复盘

- 在「面试准备」可查看当前播放设备、快捷键注册情况及所选云服务的 Key 配置状态。点「检查系统回放（4 秒）」后播放一段会议测试声音。本机显示片段时长和峰值，检查过程不调用云服务。
- 可在「术语候选」填入类似 `RAG=Grok` 的对应关系，多条用分号分隔。若命中候选词，程序保留原转写并停在确认状态。可以原样提交、改字，或采用候选词后再提交。「核实过的项目事实」可填职责、指标和明确的实验条件，开始本场后锁定。
- 默认窗口并排显示问题和回答，紧凑窗口上下排列。用「本场回看」选择、固定或切换最近问答。应用获得焦点时可用 **Ctrl+Alt+↑／↓** 浏览本场题目。深度重答会刷新同一回合，固定内容可留到场次结束。
- 云端追问可以参考最近四条题目。之前生成的回答可在本地回看。需要沿用的个人细节可以通过「纠正事实」确认后加入本场。
- 可在「口述卡」中每行填写 `问题 => 已核实的口述稿`，最多 20 张。点击「开始本场」后，快速模式遇到同一问题直接显示原稿。文字命中无需设置 API，音频命中仍需转写。相似问题和深度档走现有生成。编辑当前事实会暂停口述卡，核对草稿后重新开始本场。

## 双开与音频范围

- WASAPI 共享模式允许多个程序同时读取同一个播放端点。两份问答软件也可同时打开，各自填写资料与密钥。
- 在「设置 → 录入快捷键」直接按下自选组合，Esc 取消。支持 Ctrl、Alt、Shift 加字母、数字、空格或 F1–F11。设置保存在本机 `%LOCALAPPDATA%\SystemAudioQA\hotkey.json`，支持恢复默认和仅按钮模式。
- 两个程序需使用不同的全局热键。热键仅在缓冲时注册。如果选中的组合在缓冲时已被占用，程序会提示并允许使用按钮。缓冲中换键发生冲突会恢复原组合。
- 软件只打开 WASAPI loopback 播放端点。它读取该端点混合后的播放声音，包括同一设备上的通话、视频或通知声。Windows 默认播放端点变化时，默认模式重新连接并清空旧缓冲。若会议使用固定的非默认输出，可停止缓冲、刷新并选择该设备。
- 程序在内存保留最近的音频。触发音频提问时把选取的片段发送给设置中选定的服务转写，再把转写、锁定资料和相关上下文送给它的回答接口。只点击「保存密钥」时 API Key 才写入当前 Windows 用户的 DPAPI 密文。本场资料、原音和回合历史随程序退出清空。明确保存的记忆存在本机 SQLite，可从记忆页查看、修改和删除。

## 云服务

- DeepSeek 模式使用同一把 Key 调用文件转写和 `deepseek-flash` 流式回答。快速模式关闭思考，深度模式启用高思考力度，界面显示最终回答。转写使用触发时截取的 16 kHz 单声道 WAV。 [DeepSeek 文件转写](https://api-docs.deepseek.com/guides/asr) · [DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode)
- OpenAI 仍可从设置中选择。选择服务时，输入框切换到对应服务的独立凭据。单次请求从转写到回答都使用触发时已选定的服务。

## 共享与计时

- 在「问答」勾选「截图保护」，或在「设置」勾选「截图与共享保护」。主窗和参考资料窗都会应用 Windows 捕获排除。开启时可粘贴资料。实际共享画面以接收端为准。
- 按需开启耗时记录，使用「导出记录」把 JSONL 保存到所选位置。记录包含服务商、音频大小、反馈时延、转写可见时间、首字时间、标点所在字符偏移与时间、完成状态。首条可用答案在正式测试用逐题评分标定。问答字号在同一页可调。

## 运行与打包

使用 Windows x64、Python 3.12 及 Tk。Python 3.12 自带的 `audioop` 用于将 PCM 转成适合转写的单声道 WAV。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest -v
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm SystemAudioQA.spec
```

安装包自检会播放一句普通话测试语音、触发一次 Windows Shift+Z，并短时显示一个 64×64 测试标记用来验证本地捕获排除。自检打开两个 WASAPI 共享回放客户端，并使用模拟 API 走完整问答流程。测试时请避开正在进行的通话。自检无需 API Key。

```powershell
$report = Join-Path $PWD 'exe-smoke.json'
$check = Start-Process -FilePath .\dist\SystemAudioQA.exe -ArgumentList "--self-test `"$report`"" -PassThru -Wait -WindowStyle Hidden
Get-Content -LiteralPath $report
```

详细测试步骤和真实服务验收边界见 [测试方案](TEST_PLAN.md)。

接口文档见 [WASAPI Loopback](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)、[OpenAI 文件转写](https://developers.openai.com/api/docs/guides/transcription) 和 [Responses 流式事件](https://developers.openai.com/api/reference/resources/responses/streaming-events)。
