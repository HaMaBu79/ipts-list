# 🔧 TV360 DRM — Tài liệu kỹ thuật: 4 kênh thể thao + ON Phim Việt

> Áp dụng cho: **ON SPORTS**, **ON SPORTS+**, **ON FOOTBALL**, **ON SPORTS NEWS**, **ON Phim Việt**
> Nhóm: `VTVcab` · Nguồn: TV360 (gói **V Sport**)

---

## 🩺 Triệu chứng

Kênh hiện **màn hình đen**, không báo lỗi (Kodi/TiviMate). Mọi thứ khác vẫn bình thường.

---

## 🔎 4 tầng nguyên nhân (đều đã tìm ra & sửa)

### Tầng 1 — Nguồn fallback bị chặn UA desktop
`tv.vietanhtv.top` chặn User-Agent desktop (Chrome/Safari) và trả:
```
#EXTINF:-1, ❌ LỖI: THIẾU BỊ HOẶC ỨNG DỤNG KHÔNG ĐƯỢC HỖ TRỢ
```
→ Script cũ dùng Chrome UA → **không lấy được playlist** → không có `license_key` mới.

**Fix:** đổi sang `Dalvik/2.1.0`, kèm danh sách UA dự phòng (`UA_FALLBACKS`).

### Tầng 2 — Tài khoản chính không có gói VSport
API `get-link` của TV360 trả:
```json
{ "isAccess": false,
  "packageGroupName": "V Sport",
  "msgRegPackageGroup": "Nội dung bạn đang xem thuộc gói VSport. Vui lòng mua gói để tiếp tục xem." }
```
→ Không có `urlStreaming` → không lấy được link.

**Tài khoản có gói:** `vt032554406851` → `isAccess: true`, có `urlStreaming`.

**Fix:** thêm **PASS 0** — lấy 4 kênh thể thao từ tài khoản VSport ngay từ đầu.

### Tầng 3 — Kodi không gọi được endpoint license (màn đen)
Server clearkey **chỉ** trả key khi UA là `Dalvik/2.1.0`:
```bash
# SAI -> "File not found" (KHÔNG BÁO LỖI -> màn đen)
curl -A 'Chrome/...' 'https://tv.vietanhtv.top/tivi/cleankey.php?id=tv360173&token=...'

# ĐÚNG -> trả key thật
curl -A 'Dalvik/2.1.0' 'https://tv.vietanhtv.top/tivi/cleankey.php?id=tv360173&token=...'
# {"keys":[{"kty":"oct","kid":"P2XBB8mQTKmN3SJnHVyJww","k":"48b75Mrou_E-1qe-jmaqVQ"}],"type":"temporary"}
```
Kodi/TiviMate gửi UA riêng → `"File not found"` → **màn đen im lặng**.

**Fix:** BB2 tự fetch key (đúng UA) → giải mã KID base64url → hex → nhúng thẳng `KID:KEY` vào playlist.

### 🆕 Tầng 4 — URL manifest chưa được "token hóa" (NGUYÊN NHÂN THẬT)

**Đây là tầng gây màn đen NGAY CẢ KHI ĐÃ CÓ `KID:KEY` đúng.**

API `get-link` trả về URL manifest **gốc**:
```
https://fo-hlc.tv360.vn/bpk-tv/221/output/index.mpd?auth=...
https://rr1-ateme.tv360.vn/live/eds/222/DASH_DRM_MHL_2s/index.mpd?cdntoken=...
```

Nhưng CDN (Broadpeak BKS400 / Ateme) **redirect 307/302** sang URL hoàn toàn khác, có chứa token:
```
https://ec05-pop2-hkh.tv360.vn:443/bpk-token/<id>/bpk-tv/221/output/index.mpd?bkm-query&auth=...
https://pop21-ec1-ateme.tv360.vn:443/tok_<jwt>/live/eds/222/DASH_DRM_MHL_2s/index.mpd?...
```

**Vấn đề:** trong MPD, URL segment là **đường dẫn tương đối**:
```xml
<BaseURL>dash/</BaseURL>
<SegmentTemplate initialization="221-$RepresentationID$.dash" media="221-$RepresentationID$-$Time$.dash">
```

→ Player tải manifest (đã redirect, OK) nhưng ghép segment theo **host gốc** (`fo-hlc.tv360.vn`), **không có token**:
| CDN | Kết quả |
|---|---|
| Broadpeak | `HTTP 404 Not Found` |
| Ateme | `HTTP 403 Forbidden — Token required` |

→ **Không tải được segment nào → MÀN HÌNH ĐEN.**

**Fix:** ghi URL **đã redirect (có token)** vào playlist. Segment được ghép đúng host + tiền tố token → `HTTP 200`.

Hàm mới: `tokenize_stream_url()` trong `/opt/qweb/sync_tv360.py` (không follow redirect, đọc header `Location`).

**Kiểm chứng độ bền:** token đã token-hóa vẫn `200` sau >35 phút; cron sync mỗi 30 phút lại làm mới.

### 🆕 Tầng 5 — ON Phim Việt: URL VOD có `start`/`stop` hạn ~44 phút

TV360 có **2 channel cho cùng nội dung**:

| id | Tên | URL | Hạn |
|---|---|---|---|
| **175** | `VTVcab 2 - Phim Việt HD` | `https://lsm-g12.tv360.vn:18443/348.m3u8` (HLS) | ❌ không hạn |
| 9902 | `Phim Việt` | `vod-evg4.../master.m3u8?start=...&stop=...` | ⏳ ~44 phút |

Playlist khớp nhầm vào id 9902 → hết cửa sổ là màn đen.

**Fix:** `collect_clean_links()` — dò các channel số nhỏ, lấy bản HLS **không có `start=`/`stop=`**, map theo tên gốc rễ (`_root_name()`: bỏ tiền tố `VTVcab`/`ON`/số thứ tự) và ưu tiên bản này.

---

## ✅ Kết quả verify (giải mã thật bằng ffmpeg)

```
onsports       PLAY  size=1795588  dec_err=0   h264 1920x1080 | aac
onsportsplus   PLAY  size=887548   dec_err=0   h264 1920x1080 | aac
onfootball     PLAY  size=2760404  dec_err=0   h264 1920x1080 | aac
onsportsnews   PLAY  size=1788632  dec_err=0   h264 1920x1080 | aac
onphimviet     PLAY  size=5305172  dec_err=0   h264 1920x1080 | aac

=> 5/5 kênh PLAY ĐƯỢC
```

Lệnh verify (chạy trên BB2):
```bash
python3 /tmp/_final2.py    # script trong repo local _final2.py
```

| Kiểm tra | Kết quả |
|---|---|
| MPD (`.mpd`) trả HTTP 200 | ✅ |
| **Segment** trả HTTP 200 (có token) | ✅ |
| `cenc:default_KID` khớp `KID:KEY` | ✅ 100% |
| Giải mã CENC (ffmpeg `-cenc_decryption_key`) | ✅ 0 lỗi decode |
| Số kênh trong list | 168 |
| Dòng có `KID:KEY` | 21 |
| Key có hạn | `type: temporary` → cron 30 phút tự lấy key mới |

---

## 🔧 Code đã sửa (`/opt/qweb/sync_tv360.py`)

| Hàm / đoạn | Thay đổi |
|---|---|
| `UA_FALLBACKS` | Mảng UA: `Dalvik/2.1.0`, `VLC/3.0.20`, `okhttp/3.12.12` |
| `fetch_external_playlist()` | Dùng `UA_FALLBACKS`, thử lần lượt tới khi ra playlist hợp lệ |
| `fetch_clearkey_kidkey()` | Gọi `cleankey.php` với UA `Dalvik/2.1.0` → KID base64url → hex → `"KID:KEY"` |
| **PASS 0** | Lấy kênh `{173,183,174,170,169}` từ tài khoản VSport trước |
| **`tokenize_stream_url()`** 🆕 | Đọc header `Location` của redirect CDN → trả URL manifest **có token** |
| Ghi playlist | Nếu URL là `.mpd` → `new = tokenize_stream_url(new)` |
| **`_root_name()`** 🆕 | Chuẩn hóa tên kênh bỏ tiền tố `VTVcab`/`ON`/số thứ tự |
| **`collect_clean_links()`** 🆕 | Dò channel số nhỏ, lấy bản HLS không hạn (không `start=`/`stop=`) |
| Áp dụng bản không hạn | Nếu URL hiện tại có `start=`&`stop=` → thay bằng bản HLS |

**Backup:** `/opt/qweb/sync_tv360.py.bak.pre_token`

---

## 🧪 Lệnh kiểm tra lại

```bash
# Trên BB2
bash /opt/qweb/remote.sh sync        # xem file + cron
bash /opt/qweb/remote.sh syncnow     # lấy link ngay
grep -c 'license_key=[0-9a-f]\{32\}:' /opt/qweb/list.m3u8   # đếm dòng KID:KEY
grep -c 'bpk-token\|tok_eyJ' /opt/qweb/list.m3u8             # đếm URL đã token hóa
```

**Test nhanh MPD + segment (không cần ffmpeg):**
```bash
python3 /tmp/_quick.py
# onsports  MPD=200  SEG=200
```

**Test giải mã thật:**
```bash
# lấy KID:KEY + URL cho 1 kênh rồi chạy ffmpeg
ffmpeg -user_agent 'Dalvik/2.1.0' \
  -cenc_decryption_key <KEY_HEX> \
  -i "<URL .mpd>" -t 12 -c copy -f mpegts /tmp/out.ts
ffprobe -v error -show_entries stream=codec_name,width,height -of csv=p=0 /tmp/out.ts
```

---

## ⚠️ Lưu ý

- **Key có thời hạn** (`type: temporary`) → cron mỗi 30 phút tự lấy key mới.
- **URL đã token hóa** cũng được làm mới mỗi 30 phút cùng lúc (cron sync).
- **Gói VSport** của tài khoản `vt032554406851` có thể **hết hạn** → `isAccess` thành `false` → cần gia hạn gói trên app TV360.
- **ON Phim Việt** cần player hỗ trợ HLS fMP4 (mọi player IPTV đều được).
- Nếu vẫn đen: kiểm tra app có hỗ trợ **DASH + ClearKey** không (Kodi cần `inputstream.adaptive`).
