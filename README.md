# Piano MIDI Cleanup

[English README](README_EN.md)

目前版本 **1.0.1**。Piano MIDI Cleanup 把 AI 鋼琴轉譜的 MIDI 整理成更容易看譜、編輯和練習的版本。你可以在桌面介面中校正速度與小節線、清理疑似雜音、分配左右手，再用內建鋼琴試聽結果。

專案提供 Windows 捷徑與 macOS App 的建立腳本。先完成下方的 Python 環境安裝，再執行一次腳本，之後即可雙擊開啟。

## 它做什麼

- 修正全曲 BPM，並把第一拍對齊到小節線。
- 寫入調號，讓樂譜在轉調處換調。
- 用保守規則刪除明顯的短音、弱音等轉譜雜訊。
- 依音高與動作把音符分成 2 到 4 隻手，並用 MIDI channel 標記。
- 裁短同一隻手內因踏板延音造成的重疊，讓譜面比較乾淨。
- 提供鋼琴捲簾、文字音符清單、捲簾 PNG 等檢查工具。
- 附一個鋼琴音訊轉譜入口，使用 ByteDance 的 `piano_transcription_inference` 模型。

本工具**不含來源分離**。如果輸入是歌曲混音，請先自行把鋼琴聲部分離出來；否則其他樂器的聲音也會被當成鋼琴轉錄。

## 開始使用

第一次使用需要先準備 Python 環境；完成後即可從桌面入口開啟：

1. 開啟專案頁面：<https://github.com/hibikiwtnb/piano-midi-cleanup>。
2. 按 **Code → Download ZIP**，解壓縮到一個固定資料夾。解壓後應該看到 `README.md`、`src/`、`windows/`、`macos/` 等項目。
3. 依你的平台建立 Python 3.11 環境（見下方）。
4. 執行一次對應平台的啟動器產生器，建立 GUI 入口。

之後如果搬移資料夾或重建 Python 環境，重新執行一次產生器即可。專案內的 `.cmd` / `.command` 檔會自行切換到專案資料夾，不需要先 `cd`。

## 系統需求

- **Python 3.11**，且必須有 **tkinter**。Windows 請從 [Python 官方下載頁](https://www.python.org/downloads/windows/)選擇 64-bit Python 3.11；macOS 可用 python.org 版本，或 Homebrew 的 `python@3.11` 加 `python-tk@3.11`。
- Python 套件：`torch`、`numpy`、`mido`、`librosa`、`piano_transcription_inference`、`python-rtmidi`、`pillow`、`sounddevice`。macOS 安裝另含 `numba`、`llvmlite`、`essentia`。
- **[ffmpeg](https://ffmpeg.org/download.html)** 需在 `PATH` 上，讀取 m4a、mp3 等壓縮格式時會用到。
- **[Node.js](https://nodejs.org/en/download)**：Windows 的自動 BPM 偵測與和弦顯示需要；macOS 只有和弦顯示需要 Node.js。
- 轉譜可用 CPU；有 NVIDIA GPU 時可改用 CUDA 版 PyTorch。Linux 不在本 README 的圖形介面支援範圍內。

## Windows x64 安裝

安裝 Python 後，從開始選單開啟「命令提示字元」（**cmd.exe**），貼上下列指令。這個區塊使用 cmd 語法，請保留路徑外層的引號：

```bat
py -3.11 -m venv "%USERPROFILE%\.local\share\piano-midi-cleanup-env"
"%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe" -m pip install --upgrade pip
"%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe" -m pip install torch --index-url https://download.pytorch.org/whl/cpu
"%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe" -m pip install mido librosa piano_transcription_inference python-rtmidi pillow "sounddevice>=0.5"
```

需要 NVIDIA GPU 時，把第三行換成 PyTorch 官網目前的 CUDA wheel index，例如 `--index-url https://download.pytorch.org/whl/cu126`。

Windows 沒有 Essentia wheel，所以不安裝 `essentia`；自動 BPM 改用專案內附的 essentia.js，因此需要 Node.js。也請安裝 ffmpeg 並把它的 `bin` 資料夾加入 `PATH`。

## macOS 安裝

macOS 的 `.command` 預設會找 `~/.local/share/piano-midi-cleanup-env`。以下指令都在專案資料夾中執行。

先安裝 Python 3.11、Tk、ffmpeg 與 Node.js。使用 Homebrew 時：

```bash
brew install python@3.11 python-tk@3.11 ffmpeg node
```

如果你用 python.org 的 Python 3.11，Tk 已包含在安裝檔中；仍請自行安裝 ffmpeg 與 Node.js。若 `python3.11` 不在 `PATH`，把下方指令中的 `python3.11` 換成 `$(brew --prefix python@3.11)/bin/python3.11`。

<details>
<summary><strong>Apple Silicon（arm64）安裝</strong></summary>

Apple Silicon 不需要 Intel 的舊版套件鎖定，直接安裝目前可用的版本：

```bash
python3.11 -m venv ~/.local/share/piano-midi-cleanup-env
~/.local/share/piano-midi-cleanup-env/bin/python -m pip install --upgrade pip
~/.local/share/piano-midi-cleanup-env/bin/python -m pip install torch numpy numba llvmlite essentia mido librosa piano_transcription_inference python-rtmidi pillow "sounddevice>=0.5"
```

Apple Silicon 的自動 BPM 使用 Python Essentia；Node.js 只在顯示和弦軌時需要。

</details>

<details>
<summary><strong>Intel Mac（x86_64）安裝</strong></summary>

Intel Mac 使用最後一個支援 macOS x86_64 的 PyTorch，因此版本必須固定。`numpy` 也要留在 2 以下；`numba`、`llvmlite` 與 `essentia` 使用下列版本：

```bash
python3.11 -m venv ~/.local/share/piano-midi-cleanup-env
~/.local/share/piano-midi-cleanup-env/bin/python -m pip install --upgrade pip
~/.local/share/piano-midi-cleanup-env/bin/python -m pip install "torch==2.2.2" "numpy<2" "numba==0.62.1" "llvmlite==0.45.1" "essentia==2.1b6.dev1389" mido librosa piano_transcription_inference "python-rtmidi==1.5.8" pillow "sounddevice>=0.5"
```

請不要只升級 `torch` 或 `numpy`，否則 Intel 環境可能無法啟動。

</details>

## 啟動 GUI

### Windows

雙擊 `windows\create_shortcut.cmd`。它會在專案根目錄建立 `Piano MIDI Cleanup.lnk`，使用 `pythonw.exe` 開啟面板，不會出現命令列視窗。可以把捷徑複製到桌面或開始選單。

若捷徑啟動失敗，在專案資料夾開啟 cmd 並執行下行，可查看完整錯誤訊息：

```bat
"%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe" src\postprocess_panel.py
```

### macOS

雙擊 `macos/create_app.command`，它會在專案根目錄建立 `Piano MIDI Cleanup.app`。可以拖到 Dock 或 `/Applications`。若 App 啟動失敗，錯誤會顯示對話框，詳細輸出在 `~/Library/Logs/Piano MIDI Cleanup/panel.log`。

不想建立 App 時，也可以雙擊 `macos/postprocess_panel.command`，或執行：

```bash
~/.local/share/piano-midi-cleanup-env/bin/python src/postprocess_panel.py
```

## 日常流程

### 已經有 MIDI

1. 開啟 GUI，選擇任何要整理的 MIDI 檔案，不需要改檔名。
2. 選速度來源：
   - **手動指定速度（BPM）**：知道歌曲速度時使用。**先選檔案，再輸入 BPM**；換檔案時面板會清空 BPM 欄位。
   - **從音訊偵測速度**：選來源音訊，由程式估計速度。
   - **沿用已修正的速度**：已有同曲的速度修正版（`*_bpmfix.mid`）時使用，也適合保留手動第一拍後重跑。第一次處理且尚無這個檔案時，請選前兩項之一。
3. 初次可沿用預設步驟，按「開始處理」。需要刪減難彈音群時，再勾選「精簡為一人雙手可彈的音符」；需要三／四手時，到「細部設定 → 左右手分配 → 演奏手數」調整。
4. 處理成功後，檔名以 `_bpmfix_cleaned.mid` 結尾的 MIDI 就是完整結果。使用手動 BPM 或音訊偵測時，捲簾會先預覽速度修正版，**不會自動切到處理結果**；沿用速度時則維持目前顯示。請在檔案欄選擇處理結果來檢查；要製作樂譜時，將這個 MIDI 匯入樂譜軟體。
5. 在鋼琴捲簾中檢查音符、和弦與踏板；需要調整第一拍時，見下一節。

### 從鋼琴音訊開始

1. 先確認音訊是鋼琴獨奏或已分離的鋼琴聲部。
2. Windows 雙擊 `windows\transcribe.cmd`；macOS 雙擊 `macos/transcribe.command`。兩者都會開啟檔案選擇視窗，可以一次選多個音訊檔。`src/transcribe.py` 目前也是用檔案對話框，**沒有**無介面的音訊命令列用法。
3. 第一次轉譜會用 Python 自動下載約 **165 MB** 的模型權重，不需要 `wget`。下載失敗時會保留舊檔，之後可重試。
4. 轉譜會在音訊旁產生同名的原始 MIDI、統計報告、速度修正版與處理結果（檔名規則見下表）。這一步只做 BPM 修正、規則清理與兩手初步分手；**不對齊第一拍、不寫調號、不裁短重疊**。
5. 回到 GUI，選擇生成的處理結果，在捲簾檢查並儲存第一拍，這也會寫入調號。要再調整清理、手數或截斷時，選「沿用已修正的速度」重新處理。

若音訊旁已經有同名的原始 MIDI，轉譜入口不會重新轉錄，只會補上缺少的統計與後處理檔案。

### 第一拍、預覽與重跑

- 捲簾顯示你目前選的檔案，或本次操作明確產生的預覽檔；它不會自動改用處理結果。
- **顯示處理結果時**按「儲存第一拍」：會同時更新同曲的速度修正版與處理結果，重寫兩邊的調號，平移修改痕跡，然後預覽切到速度修正版。
- **顯示原始 MIDI 時**按「儲存第一拍」：會建立或覆寫同曲的速度修正版，**不會修改原始檔**，然後預覽切到速度修正版。
- 檔名以 `_bpmfix` 或 `_bpmfix_cleaned` 結尾時，必須找得到對應的速度修正版才能調整第一拍。
- 之後重跑請選「**沿用已修正的速度**」，才能保留手動對齊。若又從原始 MIDI 重算並開啟自動第一拍，手動結果可能被取代。
- 每次重跑都會從速度修正版重建並覆寫處理結果；請保留原始音訊、原始 MIDI 與速度修正版，不要只留處理結果。

## 輸出檔案

下表的 `*` 代表你的原始檔名，程式會保留它並加上對應後綴。

| 檔案 | 內容 | 備註 |
|:---|:---|:---|
| `*.mid` | 轉譜原始 MIDI | 盡量不要直接編輯 |
| `*_stats.txt` | 各步驟統計與執行紀錄 | 每個處理區塊會更新 |
| `*_bpmfix.mid` | 速度修正版；第一拍與調號可寫在這裡 | 後處理的重建來源；手動第一拍也寫在這裡 |
| `*_bpmfix_cleaned.mid` | 處理結果：規則清理、分手、可選截斷後的 MIDI | 每次重跑都會覆寫 |
| `*_bpmfix_cleaned.changes.json` | 捲簾「修改痕跡」用的紀錄 | 只對應同一份處理結果 |
| `*.chords.json` | 顯示過的 MIDI 的和弦分析快取 | 以 MIDI 內容與分析版本比對 |
| `*_bpmfix_cleaned_pianoroll/` | 捲簾 PNG | 只有勾選截圖時產生 |

獨立執行 `clean_midi_rules` 時，另外會在輸入檔旁產生對應的 `*_clean_report.txt`。

## 常見問題與限制

- **找不到 Python**：啟動器會依序找共用環境、專案內 `.venv`、`py -3.11` 或 `PATH` 上的 Python。請確認 Python 3.11 與 tkinter 已安裝，再重建環境。
- **`tkinter` 載入失敗**：Windows 請用 python.org 版本；macOS Homebrew 請補裝 `python-tk@3.11`。
- **m4a / mp3 讀不進來**：先安裝 ffmpeg 並確認 `ffmpeg` 指令在 `PATH` 上。
- **和弦軌沒有內容**：macOS 與 Windows 都需要 Node.js。缺少 Node.js 時只影響和弦顯示；Windows 的自動 BPM 也無法使用。
- **第一次轉譜很久或下載失敗**：模型約 165 MB，只用 Python 下載，沒有 `wget`。網路不穩時重試即可。
- **BPM 抓成一半或兩倍**：選「手動指定速度（BPM）」輸入正確速度。換檔案後 BPM 欄會清空，記得先選檔再輸入。
- **第一拍不對**：在捲簾用 ±1 拍調整並儲存。低音切分、無低音前奏、3/4 拍等情況可能判錯。
- **處理後看不到結果**：手動 BPM 或音訊處理成功後，預覽是速度修正版；請在檔案欄選擇檔名以 `_bpmfix_cleaned.mid` 結尾的處理結果。
- **重跑後手動第一拍不見了**：下一輪選「沿用已修正的速度」，不要從原始檔重算自動對齊。
- **播放不是匯出**：捲簾播放只是即時試聽，本工具不輸出 WAV、MP3 或其他音訊檔。
- **內建音源**：Coopiano 是物理建模音源，不需要下載鋼琴取樣。播放使用系統預設音訊裝置；Windows 遠端桌面工作階段必須有可用的播放裝置。
- **播放環境**：Windows x64 與 macOS 的原生音源庫已隨專案提供；跨平台與遠端桌面的系統性播放驗證仍待補齊。
- **只支援 4/4 與單一速度**：變速、自由速度、3/4 等拍號會讓小節線失準。
- **不是完成樂譜**：輸出仍是 MIDI，不是排版好的樂譜或 MusicXML。轉譜錯誤、錯音與漏音仍需人工檢查。
- **三手與四手**：分手支援 2 到 4 隻手。可彈性清理只在兩手時處理全部音符；三手時必須在分手後執行，且只處理第一位演奏者的 left 與 second_right，最上面的 right 保留不動。四手可以分手，但不支援可彈性清理。
- **截斷預設不同**：GUI 的「依踏板狀態限制截斷」預設**關閉**；命令列預設**開啟**，可用 `--no-pedal-protection` 關閉。

## 進階文件與命令列

完整規則、門檻依據與已知限制見[設計規格](docs/spec/README.md)。目前待解問題見[待解決問題](docs/open-issues.md)，清理規則細節見[清理規則](docs/cleaning-rules.md)。

統一執行器可直接用命令列跑完整流程。範例中的 `input.mid`、`input.flac` 代表你自己的檔案，請替換成實際路徑；命令列不要直接輸入上表的 `*`。以下在專案資料夾中執行；macOS 先啟用剛才安裝套件的共用環境：

```bash
. "$HOME/.local/share/piano-midi-cleanup-env/bin/activate"
python src/postprocess.py "input.mid" --audio "input.flac"
python src/postprocess.py "input.mid" --bpm 135 --hands 3
python src/postprocess.py "input_bpmfix_cleaned.mid" --playable --snapshot portrait
python src/postprocess.py --list-parameters
```

Windows 不使用上面的啟用指令，改在 cmd 中直接呼叫共用環境的 `python.exe`，路徑請加引號：

```bat
"%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe" src\postprocess.py "input.mid" --bpm 135
```

`windows\*.cmd` 與 `macos/*.command` 是可直接雙擊的獨立工具入口。`bpmfix.py` 與 `midi_workbench.py` 只有命令列用法，沒有雙擊入口。各工具與參數的對照見[執行器與工具規格](docs/spec/08-runner-and-tools.md)。

## 授權與來源

Copyright (c) 2026 hibikiwtnb and contributors.

本專案原創程式碼採用 **GNU Affero General Public License v3.0 only**（`AGPL-3.0-only`），完整條款見 [LICENSE](LICENSE)。允許商業使用；分發本程式時須依授權提供相應原始碼，修改版若透過網路與使用者互動，須依第 13 條提供原始碼取得方式。軟體不附任何保證。

轉譜使用 ByteDance 的 [piano_transcription_inference](https://github.com/qiuqiangkong/piano_transcription_inference)（MIT）與其模型權重（[Zenodo 4034264](https://zenodo.org/record/4034264)，CC BY 4.0）。內建音源 Coopiano、和弦命名 Tonal.js、和弦推斷 note-seq、Windows 自動 BPM 使用的 Essentia.js 等第三方元件保留各自授權；來源與授權全文見 [sound_sources/README.md](sound_sources/README.md)、`vendor/TONAL_LICENSE.txt`、`vendor/NOTE_SEQ_LICENSE.txt` 與 `vendor/ESSENTIA_LICENSE.txt`。本專案是個人專案，與 ByteDance 無關。
