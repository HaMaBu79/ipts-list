# ipts-list - Playlist IPTV từ TV360 (lấy link TRỰC TIẾP)

> **Thay đổi quan trọng:** link kênh không còn lấy qua `tv.vietanhtv.top` (nguồn trung gian).
> Bây giờ script gọi thẳng API của TV360:
> - `api.tv360.vn` -> danh sách kênh, nhóm kênh
> - `tv360.vn` -> link phát `.m3u8` (giải mã AES-128-ECB bằng khóa công khai của web)

## Files

| File | Vai trò |
|---|---|
| `list.m3u8` | Playlist chính (168 kênh) |
| `Listphu.m3u8` | Playlist phụ (bản sao) |
| `sync_tv360.py` | Script đồng bộ: lấy link từ TV360 -> ghi vào `list.m3u8` / `Listphu.m3u8` |
| `tv360_client.py` | Module giao tiếp TV360: dùng OTP, giữ token, lấy link, lấy danh mục |
| `auto_login_tv360.py` | Tự đăng nhập lại khi token hết hạn (không cần OTP) |
| `apply_pins.py` | Ghi đè URL của các kênh DRM sang relay `/relay/<tvg-id>.m3u8` |
| `pin_overrides.json` | Cấu hình 30 kênh DRM (nguồn gốc + license key) |
| `relay_hls.py` | Relay HLS giải mã ClearKey -> phát MPEG-TS sạch (chạy trên BB2) |
| `cron_sync_tv360.sh` | Cron job: đồng bộ + push lên GitHub |
| `.github/workflows/update-tv360.yml` | Workflow GitHub Actions (chạy tay) |

## Cấu trúc thư mục `tools/`

| File | Vai trò |
|---|---|
| `tools/relay_hls.service` | Unit systemd cho relay |
| `tools/check_urls.py` | Kiểm tra nhanh MPD + segment (không cần ffmpeg) |
| `tools/verify_channels.py` | Giải mã thật bằng ffmpeg để xác nhận kênh xem được |
| `tools/remote.sh` | Script tổng hợp kiểm tra BB2: `full\|net\|sync\|tunnel\|sys\|syncnow\|verify\|check` |

## Chạy thử

```bash
pip install cryptography

# Xem trước (không ghi file)
python3 sync_tv360.py --dry

# Ghi thật
python3 sync_tv360.py

# Ghi đè link DRM qua relay
python3 apply_pins.py
```

## Kênh nào lấy được?

| Trạng thái | Mô tả |
|---|---|
| **Miễn phí** (`needLogin = 0`) | Luôn lấy được, không cần đăng nhập |
| **Trả phí** (`needLogin = 1`) | Cần token đăng nhập TV360 |

### Lấy token đăng nhập (không cần SMS)

1. Mở <https://tv360.vn>, đăng nhập tài khoản của bạn.
2. Mở DevTools -> tab **Application** -> **Cookies** -> `https://tv360.vn`.
3. Copy giá trị 2 cookie:
   - `access-token`
   - `refresh-token`
4. Tạo file `tv360_session.json` cạnh repo:

```json
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "msisdn": "09xxxxxxxx",
  "device_id": "s%3Axxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

> File này **KHÔNG** push lên GitHub (đã thêm vào `.gitignore`).
> GitHub Actions sẽ dùng `secrets` để tạo file này khi chạy.

## GitHub Actions - Secrets cần tạo

Trong repo -> **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**:

| Secret | Giá trị | Bắt buộc? |
|---|---|---|
| `TV360_ACCESS_TOKEN` | giá trị cookie `access-token` | Không (chỉ lấy được kênh miễn phí) |
| `TV360_REFRESH_TOKEN` | giá trị cookie `refresh-token` | Không |
| `TV360_MSISDN` | số điện thoại tài khoản | Không |
| `TV360_DEVICE_ID` | giá trị cookie `device-id` | Không |

## Kênh DRM (30 kênh)

30 kênh DASH/DRM (SCTV 15/17/22/1/8/21/Phim, HBO, CNN, ...) không phát trực tiếp
được trên nhiều thiết bị vì CDN Broadpeak/Ateme cần token trên URL segment.

Cách xử lý: BB2 chạy `relay_hls.py` (ffmpeg 8.1, `-cenc_decryption_key`) giải mã
ClearKey rồi phát lại ở dạng MPEG-TS thuần qua Cloudflare tunnel:
`http://<relay-host>:8099/relay/<tvg-id>.m3u8`

`apply_pins.py` ghi đè URL của các kênh này trong playlist, giữ nguyên toàn bộ
dòng `#EXTINF`, thuộc tính `tvg-*`, group-title và định dạng CRLF.

Chi tiết kỹ thuật DRM: xem [`README_TV360_DRM.md`](README_TV360_DRM.md).

## Lưu ý

- Link TV360 có **hạn sử dụng** (token JWT trong URL). Script lấy lại mỗi 30 phút.
- Script **chỉ thay dòng URL**, giữ nguyên tên / logo / nhóm / thứ tự / `#EXTVLCOPT`.
- Mục không lấy được link -> **giữ nguyên URL cũ** (không xoá).
- File ghi qua file tạm -> kiểm tra -> mới thay thế. Lỗi -> giữ nguyên file gốc.
