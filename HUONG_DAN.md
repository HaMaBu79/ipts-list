# Dong bo URL kenh tu playlist nguon

Chuong trinh tai mot playlist **nguon** san, doi chieu voi `list.m3u8` cua
ban, va **chi thay dong URL** cua nhung kenh thuoc nhom da chon.

**Khong can tai khoan. Khong can mat khau. Khong dang nhap gi ca.**

## 1. Chay thu o may (khong ghi file)

```bash
python sync.py
```

Day la **dry-run**: khong ghi, khong push. Chi in ra muc nao se duoc doi.

Muot bo qua kiem tra segment (nhanh hon nhung it an toan):

```bash
python sync.py --no-verify
```

Kiem tra 1 kenh duy nhat:

```bash
python sync.py --only VTV3
```

## 2. Ghi that

```bash
python sync.py --apply
```

## 3. Chon kenh can theo doi

Mo `source.json`. Doi danh sach `groups`:

```json
"groups": ["VTV", "Vĩnh Long", "Địa Phương", "Địa phương",
           "VTVcab", "HTV", "SCTV"]
```

| Truong | Y nghia |
|---|---|
| `source_url` | Playlist nguon can copy URL tu do |
| `groups` | Chi kenh thuoc `group-title` trong danh sach nay moi duoc doi |
| `verify` | `true` = tai thu muc phat + 1 segment that truoc khi ghi |
| `playlist_file` | File dich, mac dinh `list.m3u8` |

**Kenh khong thuoc nhom trong `groups` → giu nguyen URL cu, khong bao gio
bi dong vao.**

Kenh thuoc nhom nhung khong tim thay trong nguon → cung giu nguyen URL cu.

## 4. Cach doi chieu

1. **`tvg-id`** — uu tien cao nhat
2. **`name`** — chi khi muc khong co `tvg-id`

Khong khop → giu URL cu. Khong doan, khong ghep theo thu tu dong.

## 5. Nguyen tac: chi doi URL

Chi dong URL cua muc duoc phep doi bi thay. Moi thu khon giu nguyen
**byte-for-byte**:

- `tvg-id`, `tvg-name`, `tvg-logo`, `group-title`
- ten kenh, comment, thu tu
- dong `#EXTVLCOPT` (User-Agent cua ban **khong bao gio bi thay**)
- dong `#EXTM3U`, `#UPDATE`

Khong them muc. Khong xoa muc. Khong sap xep lai.

Ghi file tam → kiem tra so dong va so muc → moi thay the. Bat ky loi nao
cung giu nguyen file goc.

## 6. Kiem tra muc moi

`verify: true` khong chi kiem tra HTTP 200. Chuong trinh tai thu muc phat,
xuong bien thien, roi tai **mot segment that**. Khong tai duoc → giu URL cu.

## 7. GitHub Actions — chay tu dong 3 lan/ngay

Lich chay trong `.github/workflows/update-tv360.yml`:

| Gio Viet Nam | Cron (UTC) |
|---|---|
| 06:00 | `0 23 * * *` |
| 12:00 | `0 5 * * *` |
| 18:00 | `0 11 * * *` |

Khi chay tu dong (cron), workflow **luon ghi that va push** — khong can
tick gi.

Chay thu cong: **Actions > Dong bo URL kenh > Run workflow**. Mac dinh chi
xem thu. Tick **"Ghi that vao playlist"** de ghi.

**Dong `#UPDATE HH:MM⏱DD/MM | User: HaMaBu`** o dau playlist cung duoc
tu dong cap nhat moi lan ghi that, theo gio Viet Nam. Mo kenh VTV1 se
thay ngay link vua cap nhat luc may gio.

### Bat quyen ghi cho Actions (bat buoc, lam 1 lan)

Tren GitHub vao **Settings > Actions > General > Workflow permissions**,
chon **"Read and write permissions"**.

Khong lam buoc nay, workflow chay het nhung khong push duoc.

### Khong can Secrets

Workflow nay khong dung tai khoan gi. Khong co `TV360_MSISDN`,
khong co `TV360_PASSWORD`. Khong can tao secret nao ca.

## 8. Xem ket qua

`last_report.json` — muc nao doi, muc nao giu, ly do gi.

```json
{
  "status": 0,
  "updated": [{"channel": "VTV3 HD", "old": "...", "new": "...",
               "matched_by": "tvg-id"}],
  "kept":    [{"channel": "ON SPORTS", "reason": "khong phai playlist HLS"}],
  "errors":  []
}
```

Token khong bao gio duoc ghi day du vao log hay bao cao.

## 9. Khoi phuc phien ban truoc

Moi lan commit tao mot phien ban rieng:

**History > chon commit cuoi > ... > View changes > Revert**

Hoac tai ban cu:

```
https://raw.githubusercontent.com/HaMaBu79/ipts-list/<commit-cu>/list.m3u8
```

## 10. Han che da biet

- **Phu thuoc playlist nguon.** Het nguon → het cap nhat. Kenh giu nguyen
  URL cu.
- **Link lay duoc co thoi han.** Token trong URL nguon co `expires`. Het
  han → kenh do can chay lai.
- **Khong sua `list.m3u8` tren may.** Workflow tai ban moi tu GitHub truoc
  khi chay, nen khong ghi de file cu.
- **Khong go vao `unlock v2f_v6m/`.** Thu muc do thuoc ve phan khac,
  `sync.py` khong doc gi trong do.