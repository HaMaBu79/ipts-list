# ipts-list - Playlist IPTV tu TV360 (lay link TRUC TIEP)

> **Thay doi quan trong:** link kenh khong con lay qua `tv.vietanhtv.top` (nguon trung gian).
> Bay gioc script goi thang API cua TV360:
> - `api.tv360.vn` -> danh sach kenh, nhom kenh
> - `tv360.vn` -> link phat `.m3u8` (giai ma AES-128-ECB bang khoa cong khai cua web)

## Files

| File | Vai tro |
|---|---|
| `sync_tv360.py` | Script dong bo: lay link tu TV360 -> ghi vao `list.m3u8` / `Listphu.m3u8` |
| `tv360_client.py` | Module giao tiep TV360: dung OTP, giu token, lay link, lay danh muc |
| `playlist.py` | Tien ich parse playlist (giu la cho tuong thich) |
| `.github/workflows/update-tv360.yml` | Workflow GitHub Actions chay moi 6 tieng |

## Chay thu cong

```bash
pip install cryptography

# Xem truoc (khong ghi file)
python sync_tv360.py --dry

# Ghi that
python sync_tv360.py
```

## Kenh nao lay duoc?

| Trang thai | Mo ta |
|---|---|
| **Miễn phí** (`needLogin = 0`) | Luon lay duoc, khong can dang nhap |
| **Trả phí** (`needLogin = 1`) | Can token dang nhap TV360 |

### Lay token dang nhap (khong can SMS)

1. Mo <https://tv360.vn>, dang nhap tai khoan cua ban.
2. Mo DevTools -> tab **Application** -> **Cookies** -> `https://tv360.vn`.
3. Copy gia tri 2 cookie:
   - `access-token`
   - `refresh-token`
4. Tao file `tv360_session.json` canh repo:

```json
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "msisdn": "09xxxxxxxx",
  "device_id": "s%3Axxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

> File nay **KHONG** push len GitHub (da them vao `.gitignore`).
> GitHub Actions se dung `secrets` de tao file nay khi chay.

## GitHub Actions - Secrets can tao

Trong repo -> **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**:

| Secret | Gia tri | Bat buoc? |
|---|---|---|
| `TV360_ACCESS_TOKEN` | gia tri cookie `access-token` | Khong (chi lay duoc kenh mien phi) |
| `TV360_REFRESH_TOKEN` | gia tri cookie `refresh-token` | Khong |
| `TV360_MSISDN` | so dien thoai tai khoan | Khong |
| `TV360_DEVICE_ID` | gia tri cookie `device-id` | Khong |

## Luu y

- Link TV360 co **han su dung** (token JWT trong URL). Script lay lai moi 6 tieng.
- Script **chi thay dong URL**, giu nguyen ten / logo / nhom / thu tu / `#EXTVLCOPT`.
- Muc khong lay duoc link -> **giu nguyen URL cu** (khong xoa).
- File ghi qua file tam -> kiem tra -> moi thay the. Loi -> giu nguyen file goc.
- `sync.py` + `source.json` (cach cu lay qua vietanhtv.top) da bi loai bo.
