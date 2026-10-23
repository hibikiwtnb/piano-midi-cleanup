# 鋼琴 MIDI 後處理研究地圖

檢索日期：2026-10-23。

目的：先了解每個判斷問題所屬的研究領域、代表方法、近期成果與資料限制，再決定本專案如何改進。這是研究索引，不是功能實作計畫，也不表示決定引入神經網路。

本輪查核論文摘要、部分方法與實驗章節、作者專案頁及程式庫說明。未完整精讀全部論文，未下載模型或大型資料集，未重現實驗。「近期」表示本輪找到並核對的近期工作，不宣稱已窮盡所有論文或找到每個領域的唯一最佳系統。arXiv 上傳年份與會議年份可能不同；結果以各論文自己的資料集和評估條件為限。

## 1. 本專案的判斷與研究分野

| 判斷問題 | 現有做法／限制 | 對應研究領域 | 下文 |
|---|---|---|---|
| 音訊中的鋼琴是否分離乾淨 | 外部分離；其他樂器可能漏入 | Music source separation、instrument detection | 2 |
| 音高、起音、音尾、力度、踏板 | 上游轉譜；不同模型的音尾定義不一致 | Automatic piano transcription、pedal transcription | 3 |
| BPM、半速／倍速、速度漂移 | 音訊估速與相位估計 | Tempo estimation、beat tracking | 4 |
| 第一拍與小節線 | 八分音符相位加人工確認；固定 BPM、4/4 | Downbeat tracking、meter inference | 4 |
| 哪些起音算同一組和弦／琶音 | 共用 50 ms 起音窗口 | Rhythm quantization、asynchrony、ornament recognition | 5 |
| 音符應寫多長、何時截斷重疊 | 快速音群、換和弦、外聲部等規則 | Note value recognition、voice separation | 5 |
| 真正的快速重彈或誤切 | 文件列出案例，尚無可靠判斷 | Note tracking、onset verification、transcription error detection | 6 |
| 輕音、短音、半音雙音是否錯音 | 門檻；可選刪除較弱的半音鄰音 | Acoustic evidence、error detection、harmonic analysis | 6、7 |
| 調號與轉調 | 四小節音級計數，調外音成本與轉調成本，必要時逐小節 | Global/local key estimation、pitch spelling | 7 |
| 和聲是否改變、半音是否為和弦成分 | 音級集合等局部線索，未做完整和聲分析 | Chord recognition、functional harmony | 7 |
| 左右手與多手分配 | 音域、跨度、手位移動、快速音群、束搜尋 | Hand assignment、staff/voice separation | 8 |
| 手指數、跨度、前後動作是否可彈 | 可彈性清理逐組判斷；分手另有手位移動成本 | Fingering estimation、difficulty estimation、piano reduction | 9 |
| 如何知道處理真的改善 | 統計與試聽；缺少成對人工答案 | Transcription evaluation、score alignment、listening evaluation | 10 |

圖像旋轉、報告更新和 GUI 排版屬工程功能，不列為音樂判斷研究。內建音源合成屬播放工程，不能用來證明原始音訊中的某顆音是否存在。

## 2. 鋼琴分離與樂器混淆

### 代表資源與近期工作

- **Piano Concerto Dataset（2023）**：鋼琴與管弦樂的多軌資料及分離研究，可研究有伴奏時的鋼琴抽取。[作者所屬研究機構論文](https://www.audiolabs-erlangen.de/content/resources/MIR/PCD_AudioLabs/2023_OezerSALSM_PianoConcertoDataset_TISMIR_ePrint.pdf)
- **ACMID（2025 預印本，2026 修訂）**：把分離類別擴到包含鋼琴、木吉他、電吉他等七類，研究如何自動整理較乾淨的訓練素材。論文提供資料整理程式、分類模型與權重入口；不等於公開了全部可直接再分發的音樂資料。[論文與資源入口](https://arxiv.org/abs/2510.07840)
- **2025 AMT Challenge 結果（2026 公開預印本）**：多樂器轉譜仍受複音和音色變化影響；競賽結果不能視為分離後流行鋼琴已解決。[論文](https://arxiv.org/abs/2603.27528)

### 對本專案的意義

「漏進來的吉他刷奏被轉成鋼琴和弦」跨越分離、樂器辨識與轉譜三個任務。MIDI 後處理看不到原始音色。應分開評估分離音質、漏掉的鋼琴音和最終 MIDI 錯音，不能只依分離 SDR 排名推斷轉譜效果。

本地音源比較只有少數案例，且同時改了音質與分離版本；可提示研究方向，不能推出所有歌曲都應優先換分離而不是換轉譜模型。

## 3. 音高、起音、音尾、力度與踏板

- **Onsets and Frames（2018）**：聯合起音與持續音偵測，是理解多餘起音與重彈的重要基礎。神經模型。[論文](https://arxiv.org/abs/1710.11153)
- **High-resolution Piano Transcription with Pedals（2021）**：回歸起音、音尾與踏板事件時間，是本專案相關上游系統的研究背景。神經模型。[論文](https://arxiv.org/abs/2010.01815)
- **Transkun V2／Scoring intervals using non-hierarchical transformer（ISMIR 2024）**：聲學模型給音符區間評分，semi-CRF 透過動態規劃解碼；是神經與結構化推論的混合，不是純傳統 CRF。官方說明預設套件權重不把踏板延音併入音長。[作者程式、論文與模型入口](https://github.com/Yujia-Yan/Transkun)
- **D3RM（ICASSP 2025）**：以聲學特徵為條件，透過離散擴散逐步修正鋼琴捲簾表示。研究轉譜修正，但不是任意輸入 MIDI 就能清理的通用工具。[論文與程式入口](https://arxiv.org/abs/2501.05068)
- **Streaming Piano Transcription Based on Consistent Onset and Offset Decoding with Sustain Pedal Detection（ISMIR 2024，2025 上傳）**：明確處理起音、音尾與踏板的一致性；對理解事件解碼錯誤有用。[論文](https://arxiv.org/abs/2503.01362)
- **Multi-Task Multi-Frame Visual Piano Transcription（ISMIR 2026，V2N）**：從影片預測起音、放鍵、持鍵與力度，研究音訊下難以辨認的放鍵問題。需要影片，不能直接用於我們目前只有音訊／MIDI 的案例。[論文](https://arxiv.org/abs/2608.03419)

必須區分三種時間：實際放鍵時間、聲音衰減／踏板延音結束、樂譜應寫的音符時值。研究可能只改善其中一種。轉譜模型對音尾的定義不同，不能直接比較極短音比例或同時按鍵數來判優劣。

## 4. 速度、節拍、小節第一拍與拍號

### 研究脈絡

傳統方法以起音強度、自相關、週期與動態規劃找節拍；機率模型描述速度與小節位置的連續變化。近期神經系統同時預測拍點和小節第一拍，並處理多種可能的節奏解讀。

- **Music and Probability（2007）**：貝氏調性與節拍模型的基礎參考；作者頁提供資料與程式入口。[作者頁](https://davidtemperley.com/music-and-probability/)
- **Beat This!（ISMIR 2024）**：從音訊估拍點與小節第一拍，研究不依賴 DBN 後處理的神經追蹤器。[論文](https://arxiv.org/abs/2407.21658)、[官方程式](https://github.com/CPJKU/beat_this)
- **Masked Diffusion Enables Coherent Beat Tracking（ISMIR 2026）**：針對不連貫的節拍輸出，建模多種可能的拍點網格並逐步得到一致結果。[論文](https://arxiv.org/abs/2608.04624)、[作者資源](https://github.com/fosfrancesco/md_beat_this)

### 對照現有方法

我們的八分音符相位集中程度衡量是否符合某個週期。它不等於「哪一拍是小節第一拍」的辨識準確率，更不是經校準的正確機率。半速、倍速、切分、弱起、三連音和變速應分開測試。

下游目前固定 4/4 與單一 BPM。即使外部研究能偵測變拍號，也必須先處理下游表示能力，不能只換一個偵測函數就聲稱支援。

## 5. 和弦起音分組、節奏量化、音長與重疊

- **Rhythm Transcription of Polyphonic Piano Music Based on Merged-Output HMM for Multiple Voices（2017）**：用多個隱馬可夫模型描述不同聲部，處理左右手不完全同時、複節奏等問題。傳統統計方法；論文提供程式入口。[論文](https://arxiv.org/abs/1701.08343)
- **Note Value Recognition for Piano Transcription Using Markov Random Fields（2017）**：以音高與譜面起音上下文聯合推測音符時值。傳統統計方法；論文報告相對其比較方法平均錯誤率降低約 40%，不是本專案的實測改善。[論文](https://arxiv.org/abs/1703.08144)
- **Musical Rhythm Transcription Based on Bayesian Piece-Specific Score Models Capturing Repetitions（2019／2021 修訂）**：利用同一首曲的重複結構建立貝氏節奏模型。實驗使用流行歌曲的人聲旋律，不能直接外推到完整複音鋼琴。[論文](https://arxiv.org/abs/1908.06969)
- **Joint Estimation of Note Values and Voices（ISMIR 2021）**：聯合估計音符時值與聲部，並研究對多音、漏音、速度錯誤的穩健性。採 BiLSTM，列為研究對照。[作者論文](https://eita-nakamura.github.io/articles/Hiramatsu_etal_PianoTranscription_ISMIR2021.pdf)
- **DuET（2026-10 預印本）**：聯合處理演奏 MIDI 到譜面屬性，以及拍點／小節第一拍。屬神經模型；論文報告 Gangqinpu 上 MUSTER 平均錯誤由 6.56% 降到 3.64%，ASAP 上則由 11.30% 到 11.22%，顯示收益受資料影響。[論文](https://arxiv.org/abs/2610.21467)、[作者程式與資源入口](https://github.com/wei-zeng98/DuET)

對本專案而言，固定 50 ms 的分組可能混淆和弦的非同步起音、琶音及裝飾音。時值與聲部研究提醒我們：先辨識哪些音屬於同一條音樂線，再決定是否截斷，可能比一律截到同手下一個起音更合理。這是研究推論，尚未在本地驗證。

## 6. 多餘音、半音鄰音與真假重彈

### 傳統與近期方向

- **Semi-Supervised Convolutive NMF for Automatic Piano Transcription（SMC 2022）**：用單音錄音與卷積非負矩陣分解描述鋼琴聲音的時間演變；有程式入口。無需神經網路，但需要音訊及模板，且論文指出泛化問題。[論文](https://arxiv.org/abs/2202.04989)
- **Detecting Music Performance Errors with Transformers／Polytune（AAAI 2025）**：辨識多音、漏音與錯音，提供合成錯誤資料和程式。它以參考樂譜作為比較依據；不適用於直接證明「沒有正確樂譜時，我們也能判斷任何 MIDI 的錯音」。[論文](https://arxiv.org/abs/2501.02030)、[作者程式](https://github.com/ben2002chou/Polytune)
- D3RM 與起音偵測研究可補充聲學證據，見第 3 節；同曲重複結構可補充節奏證據，見第 5 節。

### 目前能下的結論

本輪尚未找到已充分驗證、可以直接套用到「流行音樂分離鋼琴中的假快速和弦重彈」的通用後處理方法。這表示搜尋尚未找到，不表示整個領域不存在。

應區分：轉譜錯誤、原演奏失誤、真正的不協和音，以及為降低難度而刻意簡化。它們需要不同的答案。聲學上確實存在的快速和弦，也可能需要簡化，但不能因此標成錯音。

我們的力度、音長門檻是可解釋的啟發式，不能把「符合刪除規則」直接當成錯音標準答案。半音、低力度與短時值都可能是合法演奏。

## 7. 調性、調號、轉調、和弦與音名拼寫

- **Temperley 的貝氏調性模型與 Krumhansl–Schmuckler 基準**：用音級分布等線索估計調性；作者提供相關程式。適合作為不需神經網路的比較基準。[作者資源](https://davidtemperley.com/music-and-probability/)
- **Engraving Oriented Joint Estimation of Pitch Spelling and Local and Global Keys（TENOR 2024）**：已知小節線時，以動態規劃聯合估計音名拼寫、全局與局部調性；考慮實際印出的臨時記號及其小節內效力。論文有實作與評估資料入口，本輪未重現。[論文與完整方法](https://arxiv.org/html/2402.10247v1)
- **BACHI（2025 預印本／ICASSP 2026）**：符號音樂和弦辨識，先處理和弦邊界，再辨識根音、和弦種類與低音；提供修訂標註 POP909-CL。採神經模型。[論文](https://arxiv.org/abs/2510.06528)、[作者資料與模型入口](https://andyweasley2004.github.io/BACHI/)

這裡至少有四個不同任務：找調性中心、選可讀調號、判斷和弦、決定 F# 或 Gb 等音名拼寫。現在 `key_signature.py` 主要最佳化調外音數與轉調成本，並未完整解決這四個任務，也沒有完整的大小調辨識。

2024 的非神經研究尤其值得閱讀，因為它同樣關注臨時記號，卻計算實際譜面記號與拼寫，而不是每個調外音都累加一次。一般 MIDI 的 key signature 訊息也無法完整儲存逐音拼寫；要實際利用這類成果，需要確認輸出格式能否表達。

和弦分析可提供半音雙音的上下文，但辨識出一個和弦名稱不等於證明每顆音正確。BACHI 的實驗也顯示，額外加入可能出錯的調性條件不一定改善和弦結果，應避免讓前一環節的猜測變成下一環節的硬判決。

## 8. 聲部、譜表與左右手

- **Merged-Output HMM for Piano Fingering of Both Hands（ISMIR 2014）**：把兩隻手的輸出合併建模，聯合處理手別與指法，是傳統統計研究的重要起點。[會議論文集中的原論文](https://archives.ismir.net/ismir2014/2014_ISMIR_Proceedings.pdf)
- **Statistical Learning and Estimation of Piano Fingering（2020，2019 預印本）**：比較高階 HMM 與其他模型；作者公開 HMM 與和弦模型程式。[論文](https://arxiv.org/abs/1904.10237)、[程式與示例](https://statpianofingering.github.io/demo.html)
- **Cluster and Separate（ISMIR 2024）**：從已量化的符號音樂推測聲部與譜表，支援包含和弦的聲部與跨譜表聲部，採 GNN；有公開程式與預訓練模型。[論文](https://arxiv.org/abs/2407.21030)、[作者程式](https://github.com/CPJKU/piano_svsep)

必須區分：聲部是音樂線條，譜表是記譜位置，左右手是實際演奏分工；三者不總一致。不能把譜表標註當成所有音符的真實手別答案。

本專案現有分手已含束搜尋與手位移動，不是單一中央 C 切割。限制之一是同一組依音高切成連續片段；交叉手、左右手音高交錯未被完整表示。多數上述研究集中雙手，不能假定可直接處理我們的三手／四手模式。

## 9. 指法、難度、可彈性與刪音取捨

- **Statistical Piano Reduction Controlling Performance Difficulty（2018）**：用機率模型衡量難度，在難度約束下最佳化保真度；考慮音符序列與指法動作。研究原任務是合奏縮編，不是轉譜除錯。[論文](https://arxiv.org/abs/1808.05006)
- **Combining Piano Performance Dimensions for Score Difficulty Classification（2023）**：結合多種演奏面向預測難度，介紹帶 Henle 難度標註的 CIPI 資料。包含學習模型，可用於了解難度標註與評估設計。[論文](https://arxiv.org/abs/2306.08480)
- **Designing a Music Difficulty Measure for Controllable Automatic Piano Rearrangement（APSIPA ASC 2025）**：以七項特徵建立可用於小節與全曲的難度表示，並以線性迴歸估計難度。是較近期、無需神經網路的研究入口。[會議原論文](https://www.apsipa.org/proceedings/2025/papers/APSIPA2025_P441.pdf)

現在 `playable_reduction.py` 逐起音組找最少刪音，再以八度重複、外側音、力度決定取捨；尚未把前後手位與旋律連續性放進同一個刪音最佳化問題。研究中「保真度與難度之間的取捨」正好對應這個缺口。

但要把三件事分開：幾何上放得下、在指定速度下做得到、對指定程度的人適合。全曲難度等級也不等於每小節都可靠，更不是物理上絕對不可能的證明。

## 10. 評估、標準答案與資料集

### 評估研究

- **MUSTER**：以樂譜編輯距離評估多種記譜錯誤，提供評估程式；輸入為參考與估計的 MusicXML，不能當成直接比較任意 MIDI 的現成功能。[官方資源](https://amtevaluation.github.io/)
- **Towards Musically Informed Evaluation of Piano Transcription Models（ISMIR 2024）**：把節奏精度、力度與演奏法等面向拆開評估，討論資料分布改變後的表現退化。[論文](https://arxiv.org/abs/2406.08454)
- **A Dual Evaluation for Music Transcription（2026-08 預印本）**：研究譜面相似度與播放相似度的互補性，並以聽覺實驗檢驗播放指標。支持分開評估「譜好讀」與「聲音忠實」，不能把兩者混為同一個分數。[論文](https://arxiv.org/abs/2608.04511)

### 資料地圖

| 資源 | 可研究的問題 | 主要限制 |
|---|---|---|
| [MAESTRO](https://magenta.withgoogle.com/datasets/maestro) | 精確配對的鋼琴音訊、MIDI、踏板 | 主要古典獨奏，不等於分離後的流行歌曲 |
| [ASAP](https://github.com/fosfrancesco/asap-dataset) | 樂譜與演奏、拍點、小節第一拍、拍號等 | 古典鋼琴；不同類型標註與對齊精度要分別確認 |
| [PIG 與指法研究入口](https://statpianofingering.github.io/demo.html) | 多種合理指法、模型訓練與比較 | 指法不是唯一答案；注意手型與風格差異 |
| CIPI，見第 9 節 | 全曲難度分級 | 全曲等級不能直接當局部手部動作標註 |
| POP909-CL，見第 7 節 | 流行鋼琴和弦、拍點與調性等 | 人工修訂仍不代表任何和聲都只有唯一答案 |
| [PianoCoRe（TISMIR 2026）](https://arxiv.org/abs/2605.06627) | 整合大型演奏 MIDI 與樂譜對齊、品質整理 | 分層子集品質與用途不同；不能把全部當成逐音人工真值 |
| [PianoCoRe 作者資源](https://github.com/ilya16/PianoCoRe) | 上述資料與處理流程入口 | 本輪僅核對發布資訊，未下載或檢查內容 |
| Polytune 合成錯誤資料，見第 6 節 | 錯音、多音、漏音的可控實驗 | 合成錯誤與真實轉譜／分離錯誤有差距 |
| 本專案實際歌曲 | 分離漏音、快速和弦、半音鄰音等真實失敗 | 尚缺逐案例人工答案；不能用目前規則產生的結果自證 |

資料量大不等於答案準確。需要記錄資料來自真實琴鍵感測、人工譜面、人工標註、合成音訊，還是另一個模型的自動轉譜。相同曲目的不同演奏或重複片段應避免同時進入訓練與測試。

## 11. 從研究地圖得到的判讀原則

1. **先定義判斷目標。** 要忠於原演奏、整理可讀樂譜，還是改編成容易演奏的版本，可能需要不同答案。
2. **按輸入比較方法。** 只有 MIDI、另有原始音訊、已有正確樂譜、另有演奏影片，是四種不同條件。
3. **按能力比較結果。** 起音 F1、含音尾 F1、譜面時值、手別、指法、主觀難度不能直接互換。
4. **把模糊性保留下來。** 和弦、調性、手別與指法可能存在多個合理解；單一模型分數不應直接變成無條件刪音。
5. **先讀方法與限制，再選本地實驗。** 傳統模型也可能需要訓練參數；有公開程式也不表示 Windows 已能直接跑、模型已齊全或授權允許再分發。

本輪可以確認的是：各判斷環節都有相應研究脈絡，且傳統統計／最佳化與神經方法並存。尚不能據此選定哪個方法最適合我們的素材，也不把任何論文數字當成本專案已驗證的改進。

## 12. 研究方向：刪除音符造成的音樂資訊損失

### 研究問題

不同音符的刪除代價不同。希望衡量：移除某顆音或一組音，會損失多少旋律、低音、和聲、節奏或聲部資訊。暫稱「音樂資訊損失」，不預先宣稱它已有統一公式或以 bit 為單位。

### 相關研究

- **Statistical Piano Reduction Controlling Performance Difficulty（2018）**：明確以音樂重要性影響逐音刪除機率，保護旋律、低音及多樂器同時重複的音。這是可參考的非神經模型，但其角色判定與合奏縮編條件不同於本專案。[論文與重要性定義](https://www.cambridge.org/core/journals/apsipa-transactions-on-signal-and-information-processing/article/statistical-piano-reduction-controlling-performance-difficulty/EB23810939F49C01EBF8C64C57F3DCC2)
- **Onset and Contiguity（2021）**：以拍點位置、音高鄰近及時間接近等特徵選出核心音，研究旋律簡化與結構重要性。[論文](https://mtosmt.org/issues/mto.21.27.4/mto.21.27.4.carterenyi-rabinovitch.php)
- **IDyOM／音樂期待研究**：統計資訊量通常指事件的負對數機率，即驚奇度。它不等同刪除代價；錯音也可能很意外，反覆的主題也可能很重要。[研究說明](https://doi.org/10.1111/nyas.13654)

### 待研究的線索與驗證

- 音樂角色：旋律連續性、低音／根音、和弦特徵音、重拍與節奏動機。
- 音符屬性：相對力度、時值、音域；都要放在上下文中解釋，不能直接把低、短、弱定為不重要。
- 冗餘與交互作用：是否已有其他音支撐相同功能；同時刪掉數顆音的損失不一定等於逐顆損失相加。
- 以實際片段比較原版與刪音版，分別評估旋律辨識、和聲／節奏完整性及主觀忠實度。人工刪音偏好可有多個合理答案。

清理時應分開記錄「疑似錯音程度」與「若為真音，刪除的損失」。重要性較低不表示是錯音；保留／刪除的決策也應區分轉譜除錯與降低難度的改編。

## 13. 研究方向：音符可察覺性與聽覺損失

### 研究問題

一顆音單獨可聽，不代表放在複音音樂中仍可分辨。希望研究人對頻譜、音量、時長、起音與聲部位置的敏感度，估計刪除後的聽覺差異，作為與音樂結構重要性互補的成本。

### 相關研究

- **Moore／Glasberg 的部分響度與遮蔽模型**：估計目標聲音在背景中仍有多少響度及其偵測閾值，並有時變聲音的擴展。這些是心理聲學模型，不需要神經網路。[作者研究整理](https://pmc.ncbi.nlm.nih.gov/articles/PMC4227665/)
- **Harmonic, Melodic, and Frequency Height Influences in the Perception of Multivoiced Music（1994）**：鋼琴實驗中，音高改變的可察覺性受和聲、旋律及相對聲部高度共同影響。這是音高改變實驗，不是直接證明哪些音可刪。[原研究摘要](https://pubmed.ncbi.nlm.nih.gov/7971130/)
- **The Role of Harmonicity on Listeners' Ability to Hear Out Voices in Polyphonic Music（2025）**：研究諧和性與聲部數量對聲部追蹤的影響。能否分辨一條聲部與能否辨別刪音版本，仍是不同任務。[原研究](https://pmc.ncbi.nlm.nih.gov/articles/PMC12394604/)

### 待研究的線索與驗證

- 同時遮蔽、時間遮蔽，以及音符整個頻譜的貢獻；不能只看基頻或 MIDI velocity。
- 音符起音是否突出、是否位於內聲部、是否與其他音融合，以及移除後對厚度、粗糙感與和弦色彩的影響。
- 先用同一音源產生保留／刪除候選音的配對片段，以心理聲學模型排序，再以盲聽差異辨識驗證。不能只比較音訊波形差值或總音量。
- 初步可用 CoPiano 建立受控條件；結論只適用於測得的音源、踏板、音量與播放條件，之後應跨音源檢查。
- 逐顆不可察覺不保證一起刪除也不可察覺；連續簡化後需要重新評估整段。

「沒有把某顆音單獨聽出來」不等於「移除後完全無影響」。本方向衡量聽覺差異，第 12 節衡量音樂角色損失，兩者應分開研究再檢查能否結合。

## 14. 研究問題：踏板條件下的音尾截斷與聽感保留

### 已核對的程式現狀

`src/trim_overlaps.py` 的快速音群、換和弦、外聲部與超跨度規則共用踏板檢查：新舊 note-off 必須嚴格位於同一次完整的全踩踏板區間內；跨鬆踏板、半踏板、其他持音控制、同音重疊／重彈及事件邊界不明時保留音尾。重建時只移動被接受的音尾，保留未改動事件的相對順序。

分手會同步映射相容的來源踏板控制到使用它的目的 channel；來源控制衝突時停止該次分手，不猜測合併。這些保護仍沒有證明所有音源截斷前後聽感一致，釋放聲與共鳴仍需要音訊驗證。

### 需要區分的情況

- 新、舊 note-off 都在同一次持續踩下的踏板區間內：在一般延音語義下，兩者可能延到相同的鬆踏板位置；仍不能保證所有音源逐樣本或聽覺完全一致。
- 新 note-off 在踩踏板之前：可能先進入止音／釋放，不能假設之後踩踏板必然補回；重新踩踏板的行為也取決於音源。
- 新 note-off 在踩下區間內，舊 note-off 在鬆踏板之後：新版可能在鬆踏板時就開始止音，原版卻仍持鍵，會有縮短聽感的風險。
- 缺少 CC64、踏板未套用到音符 channel、半踏板、同音重彈、同 tick 邊界、CC66 持音踏板及不同音源的衰減／共鳴，都需要個別處理。

MIDI 的 CC64 是逐 channel 的延音控制；基本開關定義為 0–63 關、64–127 開。不能由 MIDI 軌道位置假設它自動影響所有聲部。[MIDI Association 控制訊息定義](https://midi.org/midi-1-0-control-change-messages)

### 後續研究與驗證方向

1. 在實際檔案上確認：音長是否已含踏板延音、CC64 是否存在、分手後控制器與音符 channel 是否對應。
2. 對每次候選截斷，同時記錄新舊 note-off、踏板邊界與同音重彈，估計持鍵時間改變及可能的發聲時間改變。
3. 把「整理譜面／估計持鍵」與「保持播放聽感」分開評估，不把兩種目標混成同一個成功條件。
4. 用相同音源與相同踏板事件，渲染截斷前後的片段，結合第 13 節的差異評估與盲聽；先涵蓋上述邊界案例，再判斷何時可安全截斷。

保守的踏板檢查已接入截斷演算法，已完成 8 首既有 MIDI 的後處理對比，以及同踏板區間、跨鬆踏板、半踏板、起音／鬆踏板邊界與聲道映射的小型事件案例驗證。聲學效果、渲染比較及其餘邊界仍需驗證。踏板快速切換的平滑尚未實作。
