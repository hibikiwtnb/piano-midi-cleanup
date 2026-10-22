# Tk Canvas Piano Roll / First Beat Workbench 計畫

## 目的

把目前第一拍調整使用的瀏覽器工作台改成純 Python / Tkinter 介面，移除本機 HTTP server、瀏覽器與 Web API 這層中介。

目前：

```text
Tkinter / CLI
  -> first_beat_ui.py
  -> 127.0.0.1 HTTP server
  -> Browser + HTML/JS Canvas
  -> HTTP API
  -> Python MIDI playback / MIDI write
```

目標：

```text
Tkinter GUI
  -> Tk Canvas Piano Roll
  -> Python playback
  -> first beat / BPM adjustment
  -> direct MIDI write
```

## 修改方向

### 新增 `src/pianoroll_canvas.py`

專門負責 Tkinter Piano Roll 顯示與互動：

- 音符矩形
- 拍線 / 小節線
- 左右手不同顯示
- 橫向縮放
- 水平 / 垂直捲動
- 播放頭
- 點擊 seek
- 播放時跟隨

優先只實作第一拍校對真正需要的功能，不追求完整 DAW Piano Roll。

預估約 300–500 行。

### 修改 `src/postprocess_panel.py`

在現有 Tkinter 面板中增加 First Beat / Piano Roll 工作區，可能做成 Notebook 頁籤。

加入：

- MIDI Piano Roll
- BPM
- 第一拍 offset
- `-1 beat` / `+1 beat`
- Play / Pause
- Apply / Cancel

直接呼叫 Python 函數，不經 HTTP。

預估約 150–250 行改動。

### 拆分 `src/first_beat_ui.py`

保留可重用的非 Web 邏輯：

- `read_roll_notes()`
- `build_bpm_fixed_midi()`
- `auto_align_first_beat()`
- first-beat stats

建議最後移到例如：

```text
src/first_beat.py
```

刪除：

- `ThreadingHTTPServer`
- `BaseHTTPRequestHandler`
- `webbrowser`
- HTML page builder
- `/api/save`
- `/api/cancel`
- `/api/synth/*`

## Web 資源處理

Tk Canvas 版功能確認可用後，再移除第一拍工作台專用 Web 資源：

```text
web/result.js
web/result.css
```

如果其他功能仍在使用這些檔案，則等用途拆分完再刪。

## Playback

第一階段可以先沿用目前 `midi_playback.py`，只把控制入口從 HTTP 改成 Python 直接呼叫。

內建鋼琴音源另行選型。目前待比較：

- 物理建模：`peterc/coopiano`
- 低容量採樣：`mda Piano`

等試聽後再決定是否取代現有 MIDI output。

## 建議實作順序

1. 抽出 `first_beat_ui.py` 的非 Web 邏輯。
2. 新增最小版 `pianoroll_canvas.py`，先只顯示 MIDI。
3. 加入 seek、播放頭、zoom、scroll。
4. 接 Play / Pause。
5. 接 BPM / first beat adjustment。
6. 接 Apply，直接覆寫 MIDI 並寫 stats。
7. 整合到 `postprocess_panel.py`。
8. 確認功能與舊 Web workbench 等價後，刪除 HTTP / browser 版本。

## 規模估計

整體屬於中等規模修改：

- 新增：約 300–500 行
- 修改：約 200–300 行
- 同時會刪除一批 HTTP / HTML / JS 膠水程式碼

核心 MIDI 處理演算法不需要重寫，主要是 UI 層與播放控制方式替換。
