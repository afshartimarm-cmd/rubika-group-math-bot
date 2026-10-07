# -*- coding: utf-8 -*-
"""
Rubika Group Math Game Bot
HTTP + Long Polling
Python 3.10+

ویژگی‌ها:
- فقط مخصوص گروه‌ها
- هر 1 تا 5 دقیقه یک سوال تصادفی
- عملیات + - × ÷
- فقط ریپلای به پیام سوال معتبر است
- جواب درست: +3 Coin و +1.5 امتیاز
- جواب غلط: -0.5 امتیاز
- نتیجه در همان پیام کاربر ریپلای می‌شود
- پیام نتیجه بعد از 2 ثانیه ویرایش می‌شود
- جلوگیری از پردازش دوباره آپدیت‌ها
- ذخیره امتیاز و Coin در JSON
- HTTP health endpoint برای Render / سرویس‌های مشابه
- بدون وابستگی به کتابخانه‌های اضافی برای API روبیکا، به جز Flask برای HTTP
"""

import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from fractions import Fraction
from pathlib import Path

try:
    from flask import Flask, jsonify
except ImportError:
    Flask = None
    jsonify = None


# =========================================================
# تنظیمات
# =========================================================

TOKEN = os.getenv("RUBIKA_TOKEN", "").strip()

# اگر متغیر محیطی RUBIKA_TOKEN تنظیم نکردی، توکن را اینجا بگذار:
if not TOKEN:
    TOKEN = "CGBHBG0RGAZZHEODXENOGNBKYMQTKALXCTOGHCVPCGVIILLEOZXPDYKJYSFYKPTD"

BASE_URL = f"https://botapi.rubika.ir/v3/{TOKEN}"

SUPPORT = "@Supportiy"

# فاصله بین سوال‌ها: تصادفی از 1 تا 5 دقیقه
MIN_QUESTION_DELAY = 60
MAX_QUESTION_DELAY = 300

# فایل‌های دائمی
DATA_FILE = Path("/tmp/rubika_math_data.json")
OFFSET_FILE = Path("/tmp/rubika_offset.txt")

# پورت HTTP برای Render و سرویس‌های مشابه
PORT = int(os.getenv("PORT", "10000"))

# فقط برای تست:
# اگر True باشد، سوال اول خیلی سریع ارسال می‌شود.
# برای حالت واقعی False باشد.
TEST_FIRST_QUESTION = False


# =========================================================
# HTTP
# =========================================================

def api_call(method, payload=None, timeout=40):
    """ارسال درخواست به Bot API روبیکا."""
    if not TOKEN or TOKEN == "PASTE_YOUR_RUBIKA_BOT_TOKEN_HERE":
        raise RuntimeError("توکن ربات داخل TOKEN قرار داده نشده است.")

    url = f"{BASE_URL}/{method}"
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Rubika-Math-Group-Bot/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        print(f"[HTTP ERROR] {e.code}: {raw[:500]}")
        return None
    except Exception as e:
        print(f"[API ERROR] {method}: {e}")
        return None


def send_message(chat_id, text, reply_to=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    if reply_to:
        payload["reply_to_message_id"] = str(reply_to)

    return api_call("sendMessage", payload)


def edit_message(chat_id, message_id, text):
    payload = {
        "chat_id": chat_id,
        "message_id": str(message_id),
        "text": text,
    }
    return api_call("editMessageText", payload)


def get_updates(offset_id=None):
    payload = {}

    if offset_id:
        payload["offset_id"] = str(offset_id)

    return api_call("getUpdates", payload, timeout=50)


# =========================================================
# ذخیره‌سازی
# =========================================================

DATA_LOCK = threading.Lock()


def load_data():
    if not DATA_FILE.exists():
        return {
            "users": {},
            "groups": {},
        }

    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("bad data")

        data.setdefault("users", {})
        data.setdefault("groups", {})
        return data

    except Exception:
        print("[DATA] فایل اطلاعات خراب/نامعتبر بود؛ فایل جدید ساخته می‌شود.")
        return {
            "users": {},
            "groups": {},
        }


DATA = load_data()


def save_data():
    with DATA_LOCK:
        temp = DATA_FILE.with_suffix(".tmp")

        with temp.open("w", encoding="utf-8") as f:
            json.dump(DATA, f, ensure_ascii=False, indent=2)

        temp.replace(DATA_FILE)


def load_offset():
    if not OFFSET_FILE.exists():
        return None

    try:
        value = OFFSET_FILE.read_text(encoding="utf-8").strip()
        return value or None
    except Exception:
        return None


def save_offset(offset_id):
    if offset_id is None:
        return

    temp = OFFSET_FILE.with_suffix(".tmp")
    temp.write_text(str(offset_id), encoding="utf-8")
    temp.replace(OFFSET_FILE)


# =========================================================
# تشخیص و استخراج Update
# =========================================================

def first_value(d, *keys):
    if not isinstance(d, dict):
        return None

    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]

    return None


def find_message_object(update):
    """ساختارهای رایج NewMessage را پیدا می‌کند."""
    if not isinstance(update, dict):
        return None

    candidates = [
        update.get("new_message"),
        update.get("message"),
        update.get("newMessage"),
    ]

    for item in candidates:
        if isinstance(item, dict):
            return item

    return None


def get_chat_id(update, message):
    return first_value(
        update,
        "chat_id",
        "object_guid",
        "group_guid",
    ) or first_value(
        message or {},
        "chat_id",
        "object_guid",
        "group_guid",
    )


def get_message_id(update, message):
    return first_value(
        message or {},
        "message_id",
        "messageId",
        "id",
    ) or first_value(
        update,
        "message_id",
        "messageId",
        "id",
    )


def get_text(message):
    if not isinstance(message, dict):
        return ""

    value = first_value(
        message,
        "text",
        "message_text",
        "body",
    )

    if isinstance(value, str):
        return value.strip()

    # بعضی ساختارها ممکن است متن را داخل message بگذارند.
    nested = message.get("message")
    if isinstance(nested, dict):
        value = first_value(nested, "text", "message_text", "body")
        if isinstance(value, str):
            return value.strip()

    return ""


def get_sender_id(update, message):
    return (
        first_value(
            message or {},
            "sender_id",
            "author_guid",
            "author_id",
            "user_id",
            "from_id",
        )
        or first_value(
            update,
            "sender_id",
            "author_guid",
            "author_id",
            "user_id",
            "from_id",
        )
    )


def get_reply_to_id(message):
    if not isinstance(message, dict):
        return None

    # ساختارهای رایج
    value = first_value(
        message,
        "reply_to_message_id",
        "reply_to",
        "reply_message_id",
    )

    if value:
        if isinstance(value, dict):
            return first_value(value, "message_id", "id")
        return str(value)

    reply = message.get("reply_to")
    if isinstance(reply, dict):
        return first_value(reply, "message_id", "id")

    return None


def is_group(update, chat_id, message):
    """
    اول از نوع چت استفاده می‌کند.
    اگر نوع موجود نباشد، GUID گروه روبیکا معمولاً با g شروع می‌شود.
    """

    for source in (update, message):
        if not isinstance(source, dict):
            continue

        for key in ("chat_type", "type", "object_type"):
            value = source.get(key)

            if isinstance(value, str):
                v = value.lower()

                if any(x in v for x in ("group", "supergroup")):
                    return True

                if any(x in v for x in ("user", "private")):
                    return False

    if isinstance(chat_id, str):
        return chat_id.lower().startswith("g")

    return False


# =========================================================
# موتور سوال
# =========================================================

OPERATORS = ["+", "-", "×", "÷"]


def make_question():
    op = random.choice(OPERATORS)

    if op == "+":
        a = random.randint(1, 50)
        b = random.randint(1, 50)
        answer = Fraction(a + b, 1)

    elif op == "-":
        a = random.randint(10, 80)
        b = random.randint(1, 50)

        # جواب منفی نباشد
        if b > a:
            a, b = b, a

        answer = Fraction(a - b, 1)

    elif op == "×":
        a = random.randint(2, 15)
        b = random.randint(2, 15)
        answer = Fraction(a * b, 1)

    else:
        # تقسیم را طوری می‌سازیم که جواب همیشه عدد صحیح باشد.
        b = random.randint(2, 12)
        answer_int = random.randint(2, 15)
        a = b * answer_int
        answer = Fraction(answer_int, 1)

    return {
        "text": f"{a} {op} {b} = ؟",
        "answer": answer,
    }


def normalize_answer(text):
    """ورودی‌هایی مثل ۱۲، 12، +12 و 12.0 را تا حد ممکن استاندارد می‌کند."""
    if not text:
        return None

    s = text.strip()

    # اعداد فارسی و عربی
    trans = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    )
    s = s.translate(trans)

    # حذف علامت مساوی و فاصله
    s = s.replace("=", "").replace("؟", "").replace("?", "").strip()

    # ممیز فارسی
    s = s.replace("٫", ".").replace(",", ".")

    # عدد اعشاری
    try:
        return Fraction(s)
    except Exception:
        return None


def answers_equal(user_text, correct):
    value = normalize_answer(user_text)
    return value is not None and value == correct


# =========================================================
# اطلاعات کاربران
# =========================================================

def user_key(chat_id, user_id):
    return f"{chat_id}:{user_id}"


def ensure_user(chat_id, user_id):
    key = user_key(chat_id, user_id)

    with DATA_LOCK:
        if key not in DATA["users"]:
            DATA["users"][key] = {
                "coins": 0,
                "score": 0.0,
                "correct": 0,
                "wrong": 0,
            }

        return DATA["users"][key]


def add_reward(chat_id, user_id, coins_delta, score_delta, correct=False):
    key = user_key(chat_id, user_id)

    with DATA_LOCK:
        if key not in DATA["users"]:
            DATA["users"][key] = {
                "coins": 0,
                "score": 0.0,
                "correct": 0,
                "wrong": 0,
            }

        user = DATA["users"][key]

        user["coins"] += coins_delta
        user["score"] = round(user["score"] + score_delta, 1)

        if correct:
            user["correct"] += 1
        else:
            user["wrong"] += 1

        coins = user["coins"]
        score = user["score"]

    save_data()
    return coins, score


# =========================================================
# سوالات فعال
# =========================================================

QUESTIONS_LOCK = threading.Lock()

# chat_id -> سوال فعال
ACTIVE_QUESTIONS = {}


def set_question(chat_id, question_message_id, question):
    with QUESTIONS_LOCK:
        ACTIVE_QUESTIONS[chat_id] = {
            "message_id": str(question_message_id),
            "answer": question["answer"],
            "question": question["text"],
            "created_at": time.time(),
            "answered": False,
        }


def get_question(chat_id):
    with QUESTIONS_LOCK:
        item = ACTIVE_QUESTIONS.get(chat_id)

        if item:
            return dict(item)

        return None


def mark_answered(chat_id, question_message_id):
    with QUESTIONS_LOCK:
        item = ACTIVE_QUESTIONS.get(chat_id)

        if not item:
            return False

        if str(item["message_id"]) != str(question_message_id):
            return False

        if item["answered"]:
            return False

        item["answered"] = True
        return True



def extract_sent_message_id(response):
    """استخراج message_id از پاسخ sendMessage در ساختارهای رایج Bot API."""
    if not isinstance(response, dict):
        return None

    # ساختار معمول: {"data": {"message_id": "..."}}
    data = response.get("data")
    if isinstance(data, dict):
        value = first_value(data, "message_id", "messageId", "id")
        if value:
            return str(value)

        # برخی پاسخ‌ها ممکن است پیام را داخل message برگردانند.
        nested = data.get("message")
        if isinstance(nested, dict):
            value = first_value(nested, "message_id", "messageId", "id")
            if value:
                return str(value)

    # fallback
    value = first_value(response, "message_id", "messageId", "id")
    return str(value) if value else None

# =========================================================
# پردازش جواب‌ها
# =========================================================

def process_answer(update, message):
    chat_id = get_chat_id(update, message)
    if not chat_id:
        return

    # فقط گروه
    if not is_group(update, chat_id, message):
        return

    text = get_text(message)
    if not text:
        return

    reply_to = get_reply_to_id(message)
    if not reply_to:
        return

    question = get_question(chat_id)
    if not question:
        return

    # فقط جواب همان سوال فعال
    if str(reply_to) != str(question["message_id"]):
        return

    sender_id = get_sender_id(update, message)
    if not sender_id:
        return

    # فقط یک نفر می‌تواند سوال را برنده شود
    if not mark_answered(chat_id, question["message_id"]):
        return

    correct = answers_equal(text, question["answer"])

    if correct:
        coins, score = add_reward(
            chat_id,
            sender_id,
            coins_delta=3,
            score_delta=1.5,
            correct=True,
        )

        # مرحله اول: نمایش نتیجه
        result = send_message(
            chat_id,
            "✅",
            reply_to=get_message_id(update, message),
        )

        result_message_id = extract_sent_message_id(result)

        # بعد از 2 ثانیه همان پیام را تغییر بده
        if result_message_id:
            time.sleep(2)

            edit_result = edit_message(
                chat_id,
                result_message_id,
                f"🏆  درست بود!\n"
                f"💰 +3 Coin   •   ⭐ +1.5 امتیاز\n"
                f"موجودی: {coins} Coin  |  امتیاز: {score:g}",
            )

            if not isinstance(edit_result, dict) or edit_result.get("status") not in (None, "OK"):
                print(f"[EDIT ERROR] editMessageText response: {edit_result}")

    else:
        coins, score = add_reward(
            chat_id,
            sender_id,
            coins_delta=0,
            score_delta=-0.5,
            correct=False,
        )

        result = send_message(
            chat_id,
            "❌",
            reply_to=get_message_id(update, message),
        )

        result_message_id = extract_sent_message_id(result)

        if result_message_id:
            time.sleep(2)

            edit_result = edit_message(
                chat_id,
                result_message_id,
                f"❌  جواب اشتباه بود!\n"
                f"⭐ -0.5 امتیاز\n"
                f"امتیاز فعلی: {score:g}",
            )

            if not isinstance(edit_result, dict) or edit_result.get("status") not in (None, "OK"):
                print(f"[EDIT ERROR] editMessageText response: {edit_result}")


# =========================================================
# /start
# =========================================================

def process_start(update, message):
    chat_id = get_chat_id(update, message)

    if not chat_id:
        return

    text = get_text(message)

    if text.lower() != "/start":
        return

    # /start فقط در پیوی جواب داده می‌شود؛
    # چون ربات مخصوص گروه است، در گروه چیزی نمی‌فرستد.
    if is_group(update, chat_id, message):
        return

    send_message(
        chat_id,
        "خوش اومدی 👋\n\n"
        "Welcome! Let's make every answer count. ✨\n"
        "اینجا قراره با چندتا سوال کوتاه، هوشتو به چالش بکشی. 🧠\n\n"
        f"🗣Support : {SUPPORT}"
    )


# =========================================================
# استخراج و پردازش Update
# =========================================================

def process_update(update):
    if not isinstance(update, dict):
        return

    message = find_message_object(update)

    if not message:
        return

    # ثبت خودکار گروه؛ سوال‌ها فقط برای گروه‌هایی فعال می‌شوند
    # که ربات در آن‌ها حداقل یک پیام جدید دریافت کرده باشد.
    chat_id = get_chat_id(update, message)
    if chat_id and is_group(update, chat_id, message):
        register_group(chat_id)

    process_start(update, message)
    process_answer(update, message)


# =========================================================
# ارسال سوال به گروه‌ها
# =========================================================

GROUPS_LOCK = threading.Lock()


def register_group(chat_id):
    with GROUPS_LOCK:
        groups = DATA["groups"]

        if chat_id not in groups:
            groups[chat_id] = {
                "enabled": True,
                "last_question": 0,
                "next_question": 0,
            }

            save_data()


def send_question_to_group(chat_id):
    question = make_question()

    response = send_message(
        chat_id,
        f"🧠 𝗤𝗨𝗜𝗭 𝗧𝗜𝗠𝗘\n\n"
        f"╭─ ✦ سوال سریع\n"
        f"│  {question['text']}\n"
        f"╰────────────\n\n"
        f"↳ روی همین پیام ریپلای کن و فقط جواب رو بفرست.\n"
        f"⏱ اولین جواب درست برنده‌ست.",
    )

    if not isinstance(response, dict):
        print(f"[QUESTION] ارسال سوال به {chat_id} ناموفق بود.")
        return False

    message_id = None

    data = response.get("data")
    if isinstance(data, dict):
        message_id = first_value(
            data,
            "message_id",
            "messageId",
            "id",
        )

    if not message_id:
        message_id = first_value(
            response,
            "message_id",
            "messageId",
            "id",
        )

    if not message_id:
        print("[QUESTION] پیام ارسال شد ولی message_id پیدا نشد.")
        return False

    set_question(chat_id, message_id, question)

    with GROUPS_LOCK:
        DATA["groups"].setdefault(
            chat_id,
            {
                "enabled": True,
                "last_question": 0,
                "next_question": 0,
            },
        )

        DATA["groups"][chat_id]["last_question"] = time.time()
        DATA["groups"][chat_id]["next_question"] = (
            time.time() + random.randint(
                MIN_QUESTION_DELAY,
                MAX_QUESTION_DELAY,
            )
        )

    save_data()

    print(
        f"[QUESTION] {chat_id} -> {question['text']} "
        f"(answer={question['answer']})"
    )

    return True


def question_scheduler():
    print("[SCHEDULER] زمان‌بندی سوالات فعال شد.")

    first_run_done = False

    while True:
        try:
            now = time.time()

            with GROUPS_LOCK:
                groups_snapshot = dict(DATA["groups"])

            for chat_id, info in groups_snapshot.items():
                if not info.get("enabled", True):
                    continue

                next_question = float(info.get("next_question", 0) or 0)

                if not first_run_done and TEST_FIRST_QUESTION:
                    next_question = 0

                if now >= next_question:
                    send_question_to_group(chat_id)

            first_run_done = True

            time.sleep(2)

        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}")
            time.sleep(5)


# =========================================================
# Polling
# =========================================================

def bootstrap_updates():
    """
    دفعه اول، آپدیت‌های قدیمی را فقط مصرف می‌کنیم تا ربات
    بعد از روشن شدن روی پیام‌های قدیمی اسپم نکند.
    """
    offset = load_offset()

    if offset:
        print(f"[BOOT] offset ذخیره‌شده: {offset}")
        return offset

    print("[BOOT] در حال تخلیه آپدیت‌های قدیمی؛ هیچ پیام قدیمی پردازش نمی‌شود...")

    current_offset = None

    for _ in range(20):
        result = get_updates(current_offset)

        if not isinstance(result, dict):
            break

        data = result.get("data")
        if not isinstance(data, dict):
            break

        updates = data.get("updates")
        next_offset = data.get("next_offset_id")

        # مهم: Rubika next_offset_id را برای درخواست بعدی می‌دهد.
        if next_offset:
            current_offset = str(next_offset)

        if not isinstance(updates, list) or not updates:
            break

        # اگر next_offset_id نبود، آخرین شناسه را فقط به عنوان
        # fallback نگه می‌داریم.
        if not next_offset:
            for update in updates:
                fallback = first_value(
                    update,
                    "update_id",
                    "updateId",
                    "id",
                )
                if fallback:
                    current_offset = str(fallback)

        if not current_offset:
            break

        save_offset(current_offset)

    print("[BOOT] آپدیت‌های قدیمی پردازش نشدند.")
    return current_offset


def polling_loop():
    print("=" * 55)
    print("       RUBIKA GROUP MATH BOT")
    print("=" * 55)
    print("ربات روشن شد.")
    print("فقط گروه‌ها فعال هستند.")
    print("در حال دریافت Update ...")
    print()

    offset = bootstrap_updates()

    while True:
        try:
            result = get_updates(offset)

            if not isinstance(result, dict):
                time.sleep(3)
                continue

            data = result.get("data")

            if not isinstance(data, dict):
                time.sleep(2)
                continue

            updates = data.get("updates")

            if not isinstance(updates, list):
                time.sleep(2)
                continue

            if not updates:
                time.sleep(1)
                continue

            # آپدیت‌ها را پردازش می‌کنیم.
            for update in updates:
                try:
                    process_update(update)
                except Exception as e:
                    print(f"[UPDATE ERROR] {e}")

            # Rubika برای batch بعدی next_offset_id می‌دهد.
            next_offset = data.get("next_offset_id")
            if next_offset:
                offset = str(next_offset)
                save_offset(offset)

        except KeyboardInterrupt:
            print("\nربات متوقف شد.")
            break

        except Exception as e:
            print(f"[POLLING ERROR] {e}")
            time.sleep(5)


# =========================================================
# HTTP Health Server
# =========================================================

def start_http_server():
    if Flask is None:
        print("[HTTP] Flask نصب نیست؛ بخش HTTP اجرا نشد.")
        print("[HTTP] برای Render این را نصب کن: pip install flask")
        return

    app = Flask(__name__)

    @app.get("/")
    def home():
        return "Rubika Group Math Bot is alive. OK"

    @app.get("/health")
    def health():
        return jsonify({
            "status": "ok",
            "bot": "rubika-group-math",
            "time": int(time.time()),
        })

    print(f"[HTTP] Server listening on 0.0.0.0:{PORT}")

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        use_reloader=False,
    )


# =========================================================
# Main
# =========================================================

def main():
    if TOKEN == "PASTE_YOUR_RUBIKA_BOT_TOKEN_HERE":
        print("❌ توکن ربات را داخل TOKEN قرار بده.")
        return

    # HTTP در Thread جدا
    http_thread = threading.Thread(
        target=start_http_server,
        daemon=True,
    )
    http_thread.start()

    # Scheduler در Thread جدا
    scheduler_thread = threading.Thread(
        target=question_scheduler,
        daemon=True,
    )
    scheduler_thread.start()

    # Polling در Thread اصلی
    polling_loop()


if __name__ == "__main__":
    main()
