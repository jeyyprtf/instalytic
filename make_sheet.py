#!/usr/bin/env python3
"""Update Google Sheet laporan reels @DRCINTAS.

Sekali jalan: tarik reels dari Instagram (via Composio) -> tulis ke sheet yang SAMA.
Reel baru otomatis nambah baris, statistik reel lama ikut ke-refresh, baris
RATA-RATA dihitung ulang. Tinggal `python3 make_sheet.py` tiap habis upload.
"""
import os, json, datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
import requests
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

HERE = os.path.dirname(os.path.abspath(__file__))

# --- Instagram (Composio) ---
COMPOSIO_KEY = os.environ["COMPOSIO_API_KEY"]
IG_USER = "juan"
CUTOFF = "2026-06-22"          # cuma track reels sejak tgl ini (4 reel awal & seterusnya)
METRICS = ["reach", "likes", "comments", "shares", "saved", "total_interactions",
           "views", "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]

# --- Google Sheet (tetap / fixed) ---
SPREADSHEET_ID = os.environ["REELS_SHEET_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
TOKEN = os.path.join(HERE, ".gsheets_token.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = ["No", "Tanggal", "Hook (baris pertama caption)", "Views", "Reach", "Likes",
           "Komentar", "Shares", "Saves", "Total Interaksi", "Engagement Rate (%)",
           "Views/Reach (replay)", "Avg Watch (detik)", "Total Watch (jam)", "Link"]


def composio(slug, args):
    r = requests.post(f"https://backend.composio.dev/api/v3/tools/execute/{slug}",
                      headers={"x-api-key": COMPOSIO_KEY, "Content-Type": "application/json"},
                      json={"user_id": IG_USER, "arguments": args}, timeout=90)
    d = r.json()
    if not d.get("successful"):
        raise SystemExit(f"{slug} gagal: {d.get('error') or d.get('data')}")
    return d["data"]


def fetch_reels():
    media = composio("INSTAGRAM_GET_USER_MEDIA", {"limit": 100})["data"]
    vids = [m for m in media if m.get("media_type") == "VIDEO" and m["timestamp"][:10] >= CUTOFF]
    vids.sort(key=lambda m: m["timestamp"], reverse=True)
    rows = []
    for m in vids:
        items = composio("INSTAGRAM_GET_POST_INSIGHTS",
                         {"ig_post_id": m["id"], "metric": METRICS})["data"]
        i = {it["name"]: it["values"][0]["value"] for it in items}
        reach, views, ti = i["reach"], i["views"], i["total_interactions"]
        rows.append([
            m["timestamp"][:10],
            (m.get("caption") or "").split("\n")[0].strip(),
            views, reach, i["likes"], i["comments"], i["shares"], i["saved"], ti,
            round(ti / reach * 100, 1) if reach else 0,
            round(views / reach, 2) if reach else 0,
            round(i["ig_reels_avg_watch_time"] / 1000, 1),
            round(i["ig_reels_video_view_total_time"] / 3600000, 1),
            m["permalink"],
        ])
    return rows


def creds():
    c = None
    if os.path.exists(TOKEN):
        c = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not c or not c.valid:
        if c and c.expired and c.refresh_token:
            c.refresh(Request())
        else:
            c = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES) \
                .run_local_server(port=0, open_browser=False)
        open(TOKEN, "w").write(c.to_json())
    return c


def main():
    data = fetch_reels()
    if not data:
        raise SystemExit("Tidak ada reel sejak CUTOFF.")

    # rakit tabel: header + baris bernomor + RATA-RATA
    grid = [HEADERS]
    for n, r in enumerate(data, 1):
        grid.append([n] + r)
    cols = lambda idx: [r[idx] for r in data]
    avg = lambda idx: round(sum(cols(idx)) / len(data), 1)
    grid.append([])
    grid.append(["", "", "RATA-RATA"] + [avg(i) for i in range(2, 13)] + [""])
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    grid.append([f"Update terakhir: {stamp}  ·  {len(data)} reels"])

    svc = build("sheets", "v4", credentials=creds())
    meta = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sh = meta["sheets"][0]["properties"]
    tab_id, tab_title = sh["sheetId"], sh["title"]

    svc.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID, range=f"'{tab_title}'").execute()
    svc.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID, range=f"'{tab_title}'!A1",
        valueInputOption="RAW", body={"values": grid}).execute()

    avg_row = len(data) + 2  # 0-based index of RATA-RATA row
    svc.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": [
        {"repeatCell": {"range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                "backgroundColor": {"red": .85, "green": .92, "blue": 1}}},
            "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
        {"updateSheetProperties": {"properties": {"sheetId": tab_id,
            "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {"range": {"sheetId": tab_id, "startRowIndex": avg_row, "endRowIndex": avg_row + 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"autoResizeDimensions": {"dimensions": {"sheetId": tab_id,
            "dimension": "COLUMNS", "startIndex": 0, "endIndex": len(HEADERS)}}},
    ]}).execute()

    print(f"OK {len(data)} reels -> https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit")


if __name__ == "__main__":
    main()
