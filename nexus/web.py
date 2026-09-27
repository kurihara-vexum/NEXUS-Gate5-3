from __future__ import annotations

import html
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .db import initialize, reset
from .service import BusinessError, DatabaseError, checkout, dashboard, return_device


ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path(os.environ.get("DB_FILE", ROOT / "data" / "nexus.sqlite3"))
AUTHENTICATED_USER_ID = int(os.environ.get("CURRENT_USER_ID", "1"))
STATUS_LABELS = {"AVAILABLE": "貸出可能", "LENT": "貸出中", "REPAIR": "修理中", "DISPOSED": "廃棄済み"}


def layout(content: str, *, title: str = "PC貸出管理") -> bytes:
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} | NEXUS</title><link rel="stylesheet" href="/static/styles.css"></head>
<body><div class="app"><aside><div class="brand"><span>N</span>NEXUS</div><nav><a class="active" href="/">PC貸出管理</a><a>貸出履歴</a><a>端末マスター</a></nav><p>IT Asset Portal</p></aside>
<main>{content}</main></div></body></html>""".encode()


def home_page(message: str = "", kind: str = "") -> bytes:
    data = dashboard(DATABASE_PATH, AUTHENTICATED_USER_ID)
    employee = data["employee"]
    devices = data["devices"]
    counts = {key: sum(device["status"] == key for device in devices) for key in STATUS_LABELS}
    notice = f'<div class="notice {html.escape(kind)}" role="alert">{html.escape(message)}</div>' if message else ""
    options = "".join(
        f'<option value="{device["id"]}">{html.escape(device["asset_no"])} / {html.escape(device["model_name"])}（{STATUS_LABELS[device["status"]]}）</option>'
        for device in devices
    )
    rows = "".join(
        f"""<tr><td><strong>{html.escape(device['asset_no'])}</strong></td><td>{html.escape(device['model_name'])}</td>
        <td><span class="badge {device['status'].lower()}">{STATUS_LABELS[device['status']]}</span></td>
        <td>{html.escape(device['borrower_name'] or '—')}</td><td>{html.escape(device['due_date'] or '—')}</td>
        <td>{f'<form method="post" action="/return"><input type="hidden" name="device_id" value="{device["id"]}"><button class="small">返却</button></form>' if employee and device['borrower_id'] == employee['id'] else ''}</td></tr>"""
        for device in devices
    )
    user_name = employee["name"] if employee else "社員情報なし"
    user_status = employee["employment_status"] if employee else "UNKNOWN"
    content = f"""
    <header><div><p class="eyebrow">IT ASSET MANAGEMENT</p><h1>PC貸出管理</h1><p>共用PCの貸出と返却を、安全に一元管理します。</p></div>
    <form method="post" action="/reset"><button class="secondary">初期状態に戻す</button></form></header>
    {notice}
    <section class="summary"><article><span>貸出可能</span><b>{counts['AVAILABLE']}</b></article><article><span>貸出中</span><b>{counts['LENT']}</b></article><article><span>修理中</span><b>{counts['REPAIR']}</b></article><article><span>登録端末</span><b>{len(devices)}</b></article></section>
    <section class="panel"><div class="panel-title"><div><p class="eyebrow">NEW LENDING</p><h2>貸出申請</h2></div><span class="user">{html.escape(user_name)} / {html.escape(user_status)}</span></div>
    <form class="loan-form" method="post" action="/confirm">
      <label>端末<select name="device_id" required>{options}</select></label>
      <label>返却予定日<input name="due_date" type="date" required><small>明日以降の日付を指定</small></label>
      <label class="wide">利用目的<input name="purpose" maxlength="100" required placeholder="例：社外打ち合わせで使用"></label>
      <button class="primary">確認画面へ</button>
    </form></section>
    <section class="panel"><div class="panel-title"><div><p class="eyebrow">DEVICES</p><h2>端末一覧</h2></div><span>{len(devices)} 台</span></div>
    <div class="table-wrap"><table><thead><tr><th>資産番号</th><th>機種</th><th>状態</th><th>借用者</th><th>返却予定日</th><th></th></tr></thead><tbody>{rows}</tbody></table></div></section>"""
    return layout(content)


def confirmation_page(fields: dict[str, str]) -> bytes:
    data = dashboard(DATABASE_PATH, AUTHENTICATED_USER_ID)
    device = next((item for item in data["devices"] if item["id"] == int(fields["device_id"])), None)
    device_name = f"{device['asset_no']} / {device['model_name']}" if device else "不明な端末"
    content = f"""<header><div><p class="eyebrow">CONFIRMATION</p><h1>申請内容の確認</h1><p>内容を確認して「貸出を確定」を押してください。</p></div></header>
    <section class="panel confirm"><dl><div><dt>端末</dt><dd>{html.escape(device_name)}</dd></div><div><dt>利用者</dt><dd>{html.escape(data['employee']['name'] if data['employee'] else '社員情報なし')}</dd></div><div><dt>返却予定日</dt><dd>{html.escape(fields['due_date'])}</dd></div><div><dt>利用目的</dt><dd>{html.escape(fields['purpose'])}</dd></div></dl>
    <form method="post" action="/lend">
      <input type="hidden" name="device_id" value="{html.escape(fields['device_id'])}"><input type="hidden" name="due_date" value="{html.escape(fields['due_date'])}"><input type="hidden" name="purpose" value="{html.escape(fields['purpose'])}">
      <div class="actions"><a class="secondary link" href="/">戻る</a><button class="primary">貸出を確定</button></div>
    </form></section>"""
    return layout(content, title="申請内容の確認")


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, status: HTTPStatus = HTTPStatus.OK, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, message: str, kind: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"/?message={quote(message)}&kind={quote(kind)}")
        self.end_headers()

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        values = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
        return {key: value[-1] for key, value in values.items()}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            query = parse_qs(parsed.query)
            self._send(home_page(query.get("message", [""])[0], query.get("kind", [""])[0]))
            return
        if parsed.path == "/static/styles.css":
            self._send((ROOT / "static" / "styles.css").read_bytes(), content_type="text/css; charset=utf-8")
            return
        self._send(b"Not Found", HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        try:
            fields = self._form()
            if self.path == "/confirm":
                self._send(confirmation_page(fields))
                return
            if self.path == "/lend":
                message = checkout(
                    DATABASE_PATH,
                    device_id=int(fields["device_id"]),
                    authenticated_user_id=AUTHENTICATED_USER_ID,
                    due_date=fields["due_date"],
                    purpose=fields["purpose"],
                )
                self._redirect(message, "success")
                return
            if self.path == "/return":
                message = return_device(
                    DATABASE_PATH,
                    device_id=int(fields["device_id"]),
                    authenticated_user_id=AUTHENTICATED_USER_ID,
                )
                self._redirect(message, "success")
                return
            if self.path == "/reset":
                reset(DATABASE_PATH)
                self._redirect("確認用データを初期状態に戻しました。", "success")
                return
            self._send(b"Not Found", HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8")
        except (BusinessError, DatabaseError) as error:
            self._redirect(str(error), "error")
        except (KeyError, TypeError, ValueError):
            self._redirect("入力内容を確認してください。", "error")

    def log_message(self, format: str, *args: object) -> None:
        print(f"[NEXUS] {format % args}")


def run() -> None:
    initialize(DATABASE_PATH)
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "3000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"NEXUS Gate5: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
