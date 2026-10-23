# MuScriptor on szserver

核對日期：2026-10-23。

## 目前狀態

| 項目 | 狀態 |
|---|---|
| 官方穩定版 | 0.4.0，2026-10-20 發布 |
| 本次安裝版本 | 0.4.0，來自 PyPI |
| 新版官方入口 | `/home/kn/.local/bin/muscriptor` |
| 新版 Python | `/data/apps/muscriptor/.venv/bin/python` |
| 舊整合程式 | `/models/apps/music-to-midi`，仍固定 0.2.2a1 |
| Large 權重 revision | `8809fdfbed2affa7ade94a7059e746e3880720e7`，與原有版本相同 |
| Large 權重大小 | 5,465,642,136 bytes，已在本機快取 |
| 本次新增 app 目錄大小 | 約 96 MB；未複製 PyTorch 或重新下載 Large |

舊版入口缺少可直接使用的全域 `muscriptor` 命令。現在可從新版官方 CLI 呼叫模型；舊 GUI 的版本鎖及整合程式尚未遷移，啟動舊 GUI 不會自動變成新版。

## 環境安排

舊環境含多個轉譜工具，使用 NumPy 1.26.4；新版 MuScriptor 在 Linux 要求 NumPy 2。為避免變更共用環境，新版安裝在 `/data/apps/muscriptor/.venv`，使用自己的 MuScriptor、NumPy 2.2.6 及更新的 FastAPI 等依賴。

新環境透過 `.pth` 讀取舊環境的套件目錄，沿用 PyTorch 2.9.1、torchaudio 2.9.1 與 CUDA 套件。這不是完全獨立的環境：仍依賴 `/models/apps/music-to-midi/.venv-szserver`，不能把舊環境刪除。新版套件優先於共用套件載入；安裝沒有改寫舊環境的套件。

舊共用環境內 aria-amt、audio-separator、transformers-kt 等既有依賴衝突，仍記錄在安裝日誌中；本次未修復其他工具，也不能據新版 CLI 可載入宣稱其他工具可正常運作。

新版啟動入口設定既有 Hugging Face 快取：

```text
HF_HOME=/models/hf-cache/music-to-midi
HF_HUB_CACHE=/models/hf-cache/music-to-midi/hub
```

Large 模型檔案位於：

```text
/models/hf-cache/music-to-midi/hub/models--MuScriptor--muscriptor-large/snapshots/8809fdfbed2affa7ade94a7059e746e3880720e7/
```

## 更新帶來什麼

依官方 release 記錄，0.3.0 增加節拍偵測、起拍修正、推論模式與介面改善；0.4.0 增加 tempo map、節拍修正、量化 MIDI 匯出及樂譜匯出。

Large 權重的上游 revision 仍與已下載版本一致，因此本次是執行程式與功能升級，沒有證據可宣稱換上更準的新權重。樂譜匯出是額外功能，本專案使用 MIDI 時不需要因此加裝 MuseScore。

## 用於後續模型比較

- 明確選擇 `large`：官方 CLI 預設是 `medium`，直接用預設不等於既有 Large。
- 鋼琴分離音軌可用官方樂器條件 `acoustic_piano`；比較時記錄是否啟用此限制。
- 原始轉譜比較應使用 `--detect-tempo false`，不啟用量化、補空隙或裁除前導小節，再統一使用本專案的 BPM 與第一拍對齊。
- 新版節拍偵測使用 Beat This；相關節拍模型未在本次執行或確認快取，首次使用可能需要額外下載。
- Large 載入及轉譜前須重新閱讀 szserver 的 `/models/apps/HARDWARE_USAGE.md` 並記錄資源使用；本次沒有佔用 GPU、啟動服務或轉譜歌曲。
- 程式碼為 MIT，模型權重為 CC BY-NC 4.0 並附模型頁使用條件；不能把程式碼授權當作權重可商用的依據。

## 已確認與尚未確認

已確認新版 CLI help、樂器列表、Python 模組匯入、實際版本與模組路徑、模型 API 簽名、硬樂器限制 API，以及現有 Large 模型與設定檔的快取路徑。舊環境仍報告 MuScriptor 0.2.2a1。

尚未執行模型載入、GPU 推論、歌曲轉譜、節拍偵測、試聽或官方 Web UI。安裝完成不等於已驗證端到端轉譜品質。

伺服器安裝紀錄位於 `/data/apps/muscriptor/`：`README.md`、`installation-status.json`、`install.log`、`transcribe-help.txt`、`requirements-lock.txt`。版本清單只涵蓋新版環境新增的套件，不是包含共用環境的完整重建鎖檔。

## 官方資料

- [版本更新記錄](https://github.com/muscriptor/muscriptor/releases)
- [PyPI](https://pypi.org/project/muscriptor/)
- [官方使用說明](https://github.com/muscriptor/muscriptor)
- [Large 模型資訊](https://huggingface.co/MuScriptor/muscriptor-large)
