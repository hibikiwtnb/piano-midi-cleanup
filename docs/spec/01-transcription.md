# 1. 上游轉譜與 MIDI 的性質

後處理的規則建立在對輸入 MIDI 的認識上。本章說明預設轉譜模型輸出的 MIDI 有什麼特性，以及哪些步驟依賴這些特性。

## 轉譜入口

`src/transcribe.py` 使用 ByteDance 的 `piano_transcription_inference`（Kong et al., *High-resolution Piano Transcription with Pedals by Regressing Onset and Offset Times*, 2021）。設備順序 CUDA → CPU；`src/transcribe_mps.py` 為 MPS → CUDA → CPU，實測 Apple MPS 比 CPU 慢，不作日常入口。

兩個入口共用 `ensure_checkpoint()`：預設位置缺少模型或檔案過小時，用 urllib 從 Zenodo 下載，核對大小與 Content-Length，完整後才原子替換；失敗時保留舊檔並清除暫存檔。不需要外部 wget。

轉譜入口在轉譜後直接執行 BPM 修正、規則清理與兩手分手，不做第一拍、調號與截斷；完整後處理用統一執行器（第 8 章）。`song.mid` 已存在時不重新轉譜，只補缺少的檔案。

## 預設模型輸出的性質

| 性質 | 內容 | 依據 |
|---|---|---|
| 音尾是聲音停止的時間 | 訓練時把落在踩踏板期間的音尾延到鬆踏板（官方程式 `extend_pedal=True`）。模型學到的「放鍵點」其實是聲音停止的時間，不是手指離鍵的時間 | 官方程式 `utils/utilities.py`、`utils/data_generator.py` |
| 延音被記兩次 | 音長已含延音，另外又寫 CC64 踏板；兩者描述同一段聲音 | 同上 |
| 音尾鎖在鬆踏板附近 | 8 首測試曲中，音尾減最近鬆踏板的分布在 ±25 ms 有尖峰，且偏向鬆踏板之後，超出背景的部分約在 100 ms 內結束 | `docs/research-landscape.md` 第 14 節 |
| 踏板只有開／關 | CC64 只寫 127 與 0，沒有半踏板 | 官方程式 `write_events_to_midi` |
| 音符與踏板分開估計 | 音符模型與踏板模型分開訓練與推論，兩者對同一段聲音的判斷可能矛盾 | 論文 III-E 節 |
| 同音不重疊 | 同一音高連續起音時先截斷前一音；40 ms 內的同音重彈偵測不到 | 論文 III-D 節 |
| 起音精確 | MAESTRO 上起音 F1：容差 10 ms 為 88.91%，20 ms 為 95.79% | 論文表 IV |
| 音尾較不精確 | 含音尾的 F1：容差 10 ms 為 51.28%，50 ms 為 79.06%，100 ms 為 83.25% | 論文表 V |
| 常見錯音 | 八度錯誤（泛音被當成音符）、短於 15 ms 的音、同音重複；也會漏掉八度疊音的高音 | 論文 IV-E 節 |

論文的數字來自 MAESTRO（古典獨奏、自動演奏鋼琴錄音）。本工具的主要輸入是從流行歌曲分離出的鋼琴，品質較差，實際準確度會低於論文數字。

論文全文的本地文字版為 `docs/paper-kong2021-high-resolution-piano-transcription.md`（不隨 repo 分發）。

## 各步驟依賴的性質

| 步驟 | 依賴 |
|---|---|
| BPM 修正（第 2 章） | MIDI 為單一速度 |
| 規則清理（第 4 章） | 極短音、極輕音多為誤報 |
| 分手、截斷的按鍵數判斷（第 6、7 章） | 音長含延音，所以按 MIDI 音長算的「同時按鍵數」會高估 |
| 截斷（第 7 章） | 音長含延音、CC64 為開／關、同音不重疊；踏板時間線用來判斷截斷是否改變聽感 |

## 其他模型

工具接受任何 MIDI，但不同模型的音長慣例不同，同一門檻的物理意義也不同：

| 模型 | 音長 | 踏板 | 力度 |
|---|---|---|---|
| ByteDance（預設） | 含踏板延音 | CC64 開／關 | 有 |
| Transkun V2 預設權重 | 不含踏板延音，較接近放鍵 | 另外輸出 | 有 |
| MuScriptor | 不含踏板延音 | 無 | 固定 100 |

比較結果見 `docs/lala-bytedance-transkun-comparison.md`。目前以 ByteDance 為主要來源；其他模型只作核對線索，不以「其他模型沒有這個音」作刪音依據。
