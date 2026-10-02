#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
بوت تلقائي وآمن لاستبدال <references /> بالقالب {{مراجع}} في ويكيبيديا العربية
===========================================================================

طبقات الحماية (كلها تعمل تلقائيًا بلا أي سؤال):
  1. بحث ذكي (CirrusSearch: insource + regex) مع استبعاد الصفحات التي فيها {{مراجع}}،
     وذاكرة محلية تمنع إعادة فحص الصفحات غير الصالحة في كل تشغيل.
  2. تحليل محلي صارم للنص: يتجاهل التعليقات و nowiki و pre ... ولا يعدّل إلا إذا كانت
     الصفحة تحتوي قائمة مراجع واحدة فقط، هي <references /> بسيط، في موضع قياسي
     (سطر مستقل مباشرة تحت عنوان قسم، وليس داخل div أو جدول أو قالب).
     أي وجود لقوالب مراجع كثيرة أو وسوم references متعددة = لا تعديل.
  3. تحقق من الخادم (expandtemplates): بعد الاستبدال يجب أن تنتج الصفحة قائمة مراجع
     واحدة بالضبط، وإلا تُترك الصفحة دون تعديل.
  4. احترام {{nobots}}، وتجاوز الصفحات المحمية والتعديلات الحديثة، وكشف تعارض التحرير،
     وإعادة قراءة الصفحة قبل الحفظ، وتحقق من النص المحفوظ بعد الحفظ.
  5. إيقاف تلقائي: رسالة جديدة في نقاش الحساب، صفحة إيقاف طارئ، حظر الحساب،
     أخطاء متتالية، أو بلوغ الحد الأقصى للتعديلات.

الاستخدام:
    python references_bot.py --selftest      اختبارات محلية بلا اتصال
    python references_bot.py --dry-run       معاينة فقط (لا يحفظ شيئًا)
    python references_bot.py                 تشغيل فعلي (حتى 20 تعديلًا)
    python references_bot.py --limit 100     غيّر الحد الأقصى (0 = بلا حد)

للإيقاف الطارئ أثناء التشغيل: أنشئ الصفحة «مستخدم:اسم_الحساب/إيقاف» بأي محتوى.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

if "--selftest" in sys.argv:  # يسمح بتشغيل الاختبارات دون user-config.py
    os.environ.setdefault("PYWIKIBOT_NO_USER_CONFIG", "1")

try:
    import pywikibot
    from pywikibot import pagegenerators
except ImportError:  # لا يمنع تشغيل --selftest
    pywikibot = None
    pagegenerators = None


# ============================================================
# الإعدادات
# ============================================================

LANG, FAMILY = "ar", "wikipedia"

TARGET_TEMPLATE = "مراجع"
NEW_TEXT = "{{مراجع}}"
EDIT_SUMMARY = "بوت: استبدال <references /> بالقالب [[قالب:مراجع]]"

# قوالب تنتج قائمة مراجع/ملاحظات. تُضاف إليها تحويلات {{مراجع}} المكتشفة من الويكي.
EXTRA_LIST_TEMPLATES = (
    "المراجع", "قائمة المراجع", "Reflist", "Refs", "Notelist",
    "ملاحظات", "قائمة الملاحظات",
)

# الاستعلامات بالترتيب: الدقيق أولًا، ثم الاحتياطي إذا فشل البحث بالتعبير النمطي.
# (الرموز < > / مُهرَّبة بـ \ لأن صياغة Lucene تحجز بعضها.)
SEARCH_QUERIES = (
    r'insource:/\<references *\/\>/ -hastemplate:"مراجع"',
    r'insource:"references" -hastemplate:"مراجع"',
)
SEARCH_TOTAL = 5000               # أقصى عدد نتائج بحث تُجلب

DEFAULT_LIMIT = 20                # أقصى تعديلات في التشغيل الواحد (0 = بلا حد)
DEFAULT_MAX_SCAN = 2000           # أقصى صفحات تُفحص في التشغيل الواحد (0 = بلا حد)
DEFAULT_DELAY = 10                # ثوانٍ بين كل تعديلين
MIN_MINUTES_SINCE_LAST_EDIT = 30  # لا نلمس صفحة عُدّلت قبل أقل من هذا (قد يحررها أحد الآن)
MAX_CONSECUTIVE_ERRORS = 5        # نتوقف بعد هذا العدد من الأخطاء المتتالية
GUARD_INTERVAL = 30               # كل كم ثانية نفحص الحظر/الرسائل/صفحة الإيقاف
CACHE_TTL_DAYS = 30               # مدة تذكّر الصفحات غير الصالحة (تُعاد بعدها أو عند تعديلها)
CACHE_MAX_ENTRIES = 100_000
SHUTOFF_PAGE = "مستخدم:{user}/إيقاف"
STATE_FILE = Path(__file__).resolve().with_name("references_bot_state.json")

REASONS = {
    "empty": "الصفحة فارغة",
    "ambiguous-markup": "تعليق أو وسم (nowiki/pre/...) غير مغلق، فالنص ملتبس",
    "no-target": "لا يوجد <references /> بسيط",
    "has-list-template": "فيها قالب قائمة مراجع (مثل {{مراجع}}) بالفعل",
    "multiple-tags": "فيها أكثر من وسم references",
    "indented": "الوسم في سطر يبدأ بمسافة",
    "inline": "الوسم ليس وحده في سطره",
    "unbalanced-template": "الوسم داخل قالب (أقواس غير متوازنة قبله)",
    "unbalanced-table": "الوسم داخل جدول",
    "unbalanced-html": "الوسم داخل وسم HTML مفتوح (div/span/...)",
    "not-after-heading": "الوسم ليس مباشرة تحت عنوان قسم",
    "wrapper-after": "بعد الوسم إغلاق لوسم/جدول/قالب",
    "self-check-failed": "فشل التحقق الذاتي من النتيجة",
    "not-article": "ليست في نطاق المقالات",
    "missing": "الصفحة غير موجودة",
    "redirect": "تحويلة",
    "content-model": "ليست ويكي‌نصًا",
    "recently-edited": "عُدّلت مؤخرًا",
    "protected": "صفحة محمية",
    "nobots": "{{nobots}} أو ما يماثله يمنع البوت",
    "server-lists": "بعد الاستبدال لن تنتج قائمة مراجع واحدة (تحقق الخادم)",
    "server-error": "تعذّر التحقق من الخادم",
    "changed-meanwhile": "تغيّرت الصفحة أثناء المعالجة",
    "save-refused": "رفض الخادم/الويكي الحفظ",
    "no-change": "لم يتغير شيء عند الحفظ",
}


def describe(reason: str) -> str:
    return REASONS.get(reason, reason)


# ============================================================
# مخرجات
# ============================================================

def say(message: str = "") -> None:
    if pywikibot is None:
        print(message)
    else:
        (getattr(pywikibot, "info", None) or pywikibot.output)(message)


def warn(message: str) -> None:
    if pywikibot is None:
        print("تحذير:", message)
    else:
        pywikibot.warning(message)


def show_diff(old: str, new: str) -> None:
    try:
        pywikibot.showDiff(old, new, context=1)
    except Exception:  # noqa: BLE001 - احتياط لاختلاف الإصدارات
        import difflib
        for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1):
            say(line)


def as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# ============================================================
# التحليل المحلي للنص (دوال نقية بلا اتصال ← قابلة للاختبار)
# ============================================================

# مناطق لا يعالج ميدياويكي محتواها كويكي‌نص: لا نعدّل داخلها ولا نعدّها.
_PROTECTED_TAGS = (
    "nowiki", "pre", "math", "syntaxhighlight", "source", "score", "templatedata",
    "timeline", "hiero", "chem", "ce", "graph", "imagemap", "includeonly",
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_PROTECTED_RE = re.compile(
    r"<(?P<t>%s)\b(?:[^>]*?/>|[^>]*>.*?</(?P=t)\s*>)" % "|".join(_PROTECTED_TAGS),
    re.DOTALL | re.IGNORECASE,
)
_LEFTOVER_RE = re.compile(r"<!--|</?(?:%s)\b" % "|".join(_PROTECTED_TAGS), re.IGNORECASE)
_NOT_NEWLINE = re.compile(r"[^\n]")

_ANY_REFERENCES_RE = re.compile(r"</?references\b[^>]*>", re.IGNORECASE)
_SIMPLE_REFERENCES_RE = re.compile(r"<references[ \t]*/[ \t]*>", re.IGNORECASE)
_LIST_FUNCTION_RE = re.compile(
    r"\{\{\s*#(?:tag\s*:\s*references|invoke\s*:\s*reflist)\b", re.IGNORECASE
)

_BLANKS = " \t\r\x00"  # \x00 = تعليق مُخفى
_HEADING_RE = re.compile(r"^={1,6}[ \t]*[^=\s][^\n]*?[ \t]*={1,6}$")
_WRAPPER_TAGS = ("div", "span", "small", "center", "font", "blockquote", "table")


def mask_protected(text: str) -> Optional[str]:
    """
    نسخة بنفس الطول تُطمس فيها التعليقات (\\x00) والوسوم المحمية (\\x01)،
    مع إبقاء أسطرها كما هي. تعيد None إذا كان النص ملتبسًا (وسم غير مغلق ...).
    """
    if "\x00" in text or "\x01" in text:
        return None

    def blank(fill: str):
        return lambda match: _NOT_NEWLINE.sub(fill, match.group(0))

    masked = _COMMENT_RE.sub(blank("\x00"), text)
    masked = _PROTECTED_RE.sub(blank("\x01"), masked)
    return None if _LEFTOVER_RE.search(masked) else masked


def build_template_regex(names: Iterable[str]) -> "re.Pattern[str]":
    """نمط يطابق استدعاء أي قالب من الأسماء (بوسائط أو بدونها، مع/بدون بادئة قالب:)."""
    alternatives = []
    for name in sorted({n.strip() for n in names if n and n.strip()}, key=len, reverse=True):
        words = re.split(r"[ _]+", name)
        alternatives.append(r"[ _]+".join(re.escape(word) for word in words))
    prefix = r"(?:(?:قالب|template)[ _]*:[ _]*)?"
    return re.compile(
        r"\{\{[ \t\r\n_]*" + prefix + "(?:" + "|".join(alternatives) + r")[ \t\r\n_]*(?=[|}])",
        re.IGNORECASE,
    )


def _meaningful_lines(block: str, reverse: bool) -> Iterator[str]:
    lines = block.split("\n")
    for line in (reversed(lines) if reverse else lines):
        stripped = line.strip(_BLANKS)
        if stripped:
            yield stripped


def _unbalanced_reason(prefix: str) -> Optional[str]:
    if prefix.count("{{") != prefix.count("}}"):
        return "unbalanced-template"
    if len(re.findall(r"^\{\|", prefix, re.M)) != len(re.findall(r"^\|\}", prefix, re.M)):
        return "unbalanced-table"
    for tag in _WRAPPER_TAGS:
        opened = len(re.findall(r"<%s\b[^>/]*>" % tag, prefix, re.IGNORECASE))
        closed = len(re.findall(r"</%s\s*>" % tag, prefix, re.IGNORECASE))
        if opened != closed:
            return "unbalanced-html"
    return None


def check_position(masked: str, start: int, end: int) -> Optional[str]:
    """
    يتأكد أن الوسم في موضع قياسي:
        == عنوان ==
        <references />
    وأنه ليس داخل div/جدول/قالب ولا ملفوفًا بقالب مثل {{بداية المراجع}}.
    يعيد None إذا كان الموضع سليمًا، وإلا رمز السبب.
    """
    line_start = masked.rfind("\n", 0, start) + 1
    line_end = masked.find("\n", end)
    if line_end == -1:
        line_end = len(masked)

    if masked[line_start:line_start + 1] in (" ", "\t"):
        return "indented"
    if (masked[line_start:start] + masked[end:line_end]).strip(_BLANKS):
        return "inline"

    reason = _unbalanced_reason(masked[:start])
    if reason:
        return reason

    previous = next(_meaningful_lines(masked[:line_start], reverse=True), None)
    if previous is None or not _HEADING_RE.match(previous):
        return "not-after-heading"

    following = next(_meaningful_lines(masked[line_end + 1:], reverse=False), None)
    if following and following.startswith(("</", "|", "!", "}}")):
        return "wrapper-after"
    return None


@dataclass
class Analysis:
    ok: bool
    reason: str
    new_text: str = ""
    span: tuple = (0, 0)


def analyze_text(text: str, template_re: "re.Pattern[str]") -> Analysis:
    """القرار المحلي: هل نستبدل؟ وكيف؟ (بلا أي اتصال بالشبكة)."""
    if not text or not text.strip():
        return Analysis(False, "empty")

    masked = mask_protected(text)
    if masked is None:
        return Analysis(False, "ambiguous-markup")

    simple = list(_SIMPLE_REFERENCES_RE.finditer(masked))
    if not simple:
        return Analysis(False, "no-target")

    list_templates = list(template_re.finditer(masked)) + list(_LIST_FUNCTION_RE.finditer(masked))
    if list_templates:
        return Analysis(False, "has-list-template")

    if len(simple) != 1 or len(list(_ANY_REFERENCES_RE.finditer(masked))) != 1:
        return Analysis(False, "multiple-tags")

    match = simple[0]
    reason = check_position(masked, match.start(), match.end())
    if reason:
        return Analysis(False, reason)

    new_text = text[:match.start()] + NEW_TEXT + text[match.end():]

    # تحقق ذاتي: لا وسم references متبقٍ، وقالب واحد بالضبط، والطول كما هو متوقع
    new_masked = mask_protected(new_text)
    expected_length = len(text) - (match.end() - match.start()) + len(NEW_TEXT)
    if (
        new_masked is None
        or _ANY_REFERENCES_RE.search(new_masked)
        or len(list(template_re.finditer(new_masked))) != 1
        or len(new_text) != expected_length
    ):
        return Analysis(False, "self-check-failed")

    return Analysis(True, "ok", new_text, (match.start(), match.end()))


# ============================================================
# الذاكرة الدائمة (الصفحات التي فُحصت ولم تصلح)
# ============================================================

class State:
    def __init__(self, path: Path, ttl_days: int = CACHE_TTL_DAYS):
        self.path = Path(path)
        self.ttl = timedelta(days=ttl_days)
        self.checked: dict = {}
        self.dirty = False
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data.get("checked"), dict):
                self.checked = data["checked"]
        except (OSError, ValueError, AttributeError):
            pass

    def seen(self, title: str, revid) -> bool:
        entry = self.checked.get(title)
        if not isinstance(entry, dict) or entry.get("rev") != revid:
            return False
        try:
            stamp = datetime.fromisoformat(entry["ts"])
        except (KeyError, ValueError, TypeError):
            return False
        return datetime.now(timezone.utc) - stamp < self.ttl

    def mark(self, title: str, revid, reason: str) -> None:
        self.checked[title] = {
            "rev": revid,
            "why": reason,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        if len(self.checked) > CACHE_MAX_ENTRIES:
            newest = sorted(self.checked.items(), key=lambda kv: kv[1].get("ts", ""), reverse=True)
            self.checked = dict(newest[: CACHE_MAX_ENTRIES * 4 // 5])
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "checked": self.checked}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)  # كتابة ذرّية: لا يفسد الملف عند الانقطاع
        self.dirty = False


# ============================================================
# الويكي: الحراسة، التحقق من الخادم، البحث
# ============================================================

class StopRun(Exception):
    """يُرفع لإيقاف التشغيل بأمان."""


class Guard:
    """فحوص الإيقاف التلقائي: حظر الحساب، رسائل جديدة، صفحة الإيقاف الطارئ."""

    def __init__(self, site, shutoff_title: Optional[str], ignore_messages: bool = False,
                 interval: float = GUARD_INTERVAL):
        self.site = site
        self.shutoff_title = shutoff_title
        self.ignore_messages = ignore_messages
        self.interval = interval
        self._last = float("-inf")

    def check(self, force: bool = False) -> Optional[str]:
        now = time.monotonic()
        if not force and now - self._last < self.interval:
            return None
        self._last = now

        try:
            reply = self.site.simple_request(
                action="query", meta="userinfo", uiprop="hasmsg|blockinfo"
            ).submit()
            info = reply["query"]["userinfo"]
        except Exception as err:  # noqa: BLE001 - عند الشك نتوقف
            return f"تعذّر فحص حالة الحساب ({err})"

        if "blockedby" in info or "blockid" in info:
            return "الحساب محظور"
        if not self.ignore_messages and info.get("messages") not in (None, False):
            return ("رسالة جديدة في صفحة نقاش الحساب. اقرأها ثم أعد التشغيل "
                    "(أو استخدم --ignore-messages)")

        if self.shutoff_title:
            try:
                page = pywikibot.Page(self.site, self.shutoff_title)  # كائن جديد = بيانات حديثة
                if page.exists() and page.text.strip():
                    return f"صفحة الإيقاف الطارئ «{self.shutoff_title}» تحتوي نصًا"
            except Exception as err:  # noqa: BLE001
                return f"تعذّر قراءة صفحة الإيقاف ({err})"
        return None


def discover_list_templates(site) -> set:
    """أسماء قوالب القوائم: {{مراجع}} + تحويلاتها الفعلية من الويكي + قائمة احتياطية."""
    names = {TARGET_TEMPLATE, *EXTRA_LIST_TEMPLATES}
    try:
        template = pywikibot.Page(site, TARGET_TEMPLATE, ns=10)
        for redirect in template.backlinks(follow_redirects=False, filter_redirects=True,
                                           namespaces=[10]):
            names.add(redirect.title(with_ns=False))
    except Exception as err:  # noqa: BLE001
        warn(f"تعذّر جلب تحويلات القالب ({err}); سأكتفي بالأسماء الافتراضية.")
    return names


def server_list_count(site, text: str, title: Optional[str] = None) -> Optional[int]:
    """عدد وسوم <references> بعد أن يوسّع الخادم كل القوالب (None عند الفشل أو الالتباس)."""
    try:
        expanded = site.expand_text(text, title=title)
    except Exception as err:  # noqa: BLE001
        warn(f"فشل expandtemplates: {err}")
        return None
    masked = mask_protected(expanded)
    if masked is None:
        return None
    return len(re.findall(r"<references\b", masked, re.IGNORECASE))


def preflight(site, args, dry_run: bool) -> Optional["re.Pattern[str]"]:
    """كل الفحوص قبل أي عمل. يعيد نمط قوالب القوائم، أو None للإيقاف."""
    if site.family.name != FAMILY or site.code != LANG:
        warn("الموقع ليس ويكيبيديا العربية.")
        return None

    site.login()
    if not site.logged_in():
        warn("لم يتم تسجيل الدخول. راجع user-config.py.")
        return None
    say(f"الحساب: {site.user()}")

    if not site.has_right("bot"):
        if dry_run:
            say("ملاحظة: الحساب بلا صلاحية بوت (مقبول في وضع المعاينة).")
        elif not args.allow_non_bot:
            warn("الحساب بلا صلاحية بوت، فلن أعدّل. استخدم --dry-run، "
                 "أو --allow-non-bot إن كنت تقصد ذلك.")
            return None

    if not pywikibot.Page(site, TARGET_TEMPLATE, ns=10).exists():
        warn(f"القالب {NEW_TEXT} غير موجود في الويكي.")
        return None

    names = discover_list_templates(site)
    say(f"أسماء قوالب القوائم المعروفة: {len(names)}")

    if not args.no_server_verify:
        count = server_list_count(site, NEW_TEXT)
        if count != 1:
            warn(f"{NEW_TEXT} لا يتوسّع في الخادم إلى قائمة مراجع واحدة (النتيجة: {count}). "
                 "لن أعمل دون هذا التحقق؛ استخدم --no-server-verify فقط إن كنت متأكدًا.")
            return None
    return build_template_regex(names)


@dataclass
class Context:
    site: object
    template_re: "re.Pattern[str]"
    state: State
    guard: Guard
    dry_run: bool
    delay: float
    min_age_minutes: int
    verify_server: bool
    details: bool
    save_kwargs: dict
    abort: Optional[str] = None
    last_save: float = 0.0

    def remember(self, title: str, revid, reason: str) -> None:
        if not self.dry_run and revid is not None:
            self.state.mark(title, revid, reason)


def iter_candidates(ctx: Context, stats: Counter, search_total: int = SEARCH_TOTAL) -> Iterator:
    """صفحات مرشّحة من البحث، بعد استبعاد ما فُحص سابقًا، مع جلب النص على دفعات."""
    seen_titles: set = set()

    def raw() -> Iterator:
        for query in SEARCH_QUERIES:
            say(f"بحث: {query}")
            try:
                for page in ctx.site.search(query, namespaces=[0], total=search_total):
                    yield page
                return
            except Exception as err:  # noqa: BLE001
                warn(f"تعذّر تنفيذ البحث ({err}).")
        say("لا توجد طريقة بحث ناجحة.")

    def unseen() -> Iterator:
        for page in raw():
            title = page.title()
            if title in seen_titles:
                continue
            seen_titles.add(title)
            try:
                revid = page.latest_revision_id
            except Exception:  # noqa: BLE001
                revid = None
            if revid is not None and ctx.state.seen(title, revid):
                stats["cached"] += 1
                continue
            yield page

    return pagegenerators.PreloadingGenerator(unseen(), groupsize=50)


# ============================================================
# معالجة صفحة واحدة
# ============================================================

def process_page(page, ctx: Context) -> tuple:
    """يعيد (النتيجة, السبب). النتائج: edited | would-edit | skip"""
    title = page.title()

    if page.namespace() != 0:
        return "skip", "not-article"
    if not page.exists():
        return "skip", "missing"
    if page.isRedirectPage():
        return "skip", "redirect"
    if getattr(page, "content_model", "wikitext") != "wikitext":
        return "skip", "content-model"

    text = page.text
    revid = page.latest_revision_id

    # 1) القرار المحلي (الأرخص أولًا)
    analysis = analyze_text(text, ctx.template_re)
    if not analysis.ok:
        ctx.remember(title, revid, analysis.reason)
        return "skip", analysis.reason

    # 2) فحوص من الويكي
    age = datetime.now(timezone.utc) - as_utc(page.latest_revision.timestamp)
    if age < timedelta(minutes=ctx.min_age_minutes):
        return "skip", "recently-edited"          # لا نحفظها: ستصلح لاحقًا

    if page.protection().get("edit"):
        ctx.remember(title, revid, "protected")
        return "skip", "protected"

    if not page.botMayEdit():
        ctx.remember(title, revid, "nobots")
        return "skip", "nobots"

    # 3) تحقق الخادم: الصفحة بعد الاستبدال = قائمة مراجع واحدة بالضبط
    if ctx.verify_server:
        count = server_list_count(ctx.site, analysis.new_text, title)
        if count is None:
            return "skip", "server-error"
        if count != 1:
            ctx.remember(title, revid, "server-lists")
            return "skip", "server-lists"

    if ctx.dry_run:
        show_diff(text, analysis.new_text)
        return "would-edit", "ok"

    # 4) آخر فحوص قبل الحفظ
    problem = ctx.guard.check(force=True)
    if problem:
        raise StopRun(problem)

    if page.get(force=True) != text:              # قراءة جديدة من الخادم
        return "skip", "changed-meanwhile"

    pause = ctx.delay - (time.monotonic() - ctx.last_save)
    if ctx.last_save and pause > 0:
        time.sleep(pause)

    page.text = analysis.new_text
    try:
        page.save(**ctx.save_kwargs)              # basetimestamp يكشف تعارض التحرير
    except pywikibot.exceptions.PageSaveRelatedError as err:
        warn(f"رُفض الحفظ في «{title}»: {type(err).__name__}")
        ctx.remember(title, revid, "save-refused")
        return "skip", "save-refused"
    ctx.last_save = time.monotonic()

    # 5) تحقق بعد الحفظ
    new_revid = page.latest_revision_id
    if new_revid == revid:
        ctx.remember(title, revid, "no-change")
        return "skip", "no-change"
    ctx.remember(title, new_revid, "edited")

    try:
        saved = page.getOldVersion(new_revid)
    except Exception:  # noqa: BLE001 - التحقق اللاحق اختياري
        saved = None
    if saved and saved.rstrip() != analysis.new_text.rstrip():
        ctx.abort = (f"النص المحفوظ في «{title}» (المراجعة {new_revid}) "
                     "يختلف عمّا أُرسل! راجعه يدويًا.")
    return "edited", "ok"


def run(ctx: Context, limit: int, max_scan: int, search_total: int = SEARCH_TOTAL):
    stats: Counter = Counter()
    reasons: Counter = Counter()
    stop_reason, clean = "انتهت نتائج البحث", True
    consecutive_errors = 0

    try:
        for page in iter_candidates(ctx, stats, search_total):
            if max_scan and stats["scanned"] >= max_scan:
                stop_reason = f"بلغتُ الحد الأقصى للفحص ({max_scan})"
                break
            if limit and stats["edited"] + stats["would_edit"] >= limit:
                stop_reason = f"بلغتُ الحد الأقصى للتعديلات ({limit})"
                break
            problem = ctx.guard.check()
            if problem:
                stop_reason, clean = problem, False
                break

            stats["scanned"] += 1
            title = page.title()
            try:
                outcome, reason = process_page(page, ctx)
                consecutive_errors = 0
            except StopRun as stop:
                stop_reason, clean = str(stop), False
                break
            except Exception as err:  # noqa: BLE001
                stats["errors"] += 1
                consecutive_errors += 1
                warn(f"خطأ في «{title}»: {type(err).__name__}: {err}")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    stop_reason = f"{MAX_CONSECUTIVE_ERRORS} أخطاء متتالية"
                    clean = False
                    break
                continue

            if outcome == "edited":
                stats["edited"] += 1
                say(f"[تعديل] {title}")
            elif outcome == "would-edit":
                stats["would_edit"] += 1
                say(f"[معاينة] {title}")
            else:
                reasons[reason] += 1
                if ctx.details:
                    say(f"[تخطي] {title}: {describe(reason)}")

            if ctx.abort:
                stop_reason, clean = ctx.abort, False
                break
            if stats["scanned"] % 25 == 0:
                say(f"... فُحصت {stats['scanned']} | عُدّلت {stats['edited']}")
    except KeyboardInterrupt:
        stop_reason = "أوقفه المستخدم (Ctrl+C)"
    finally:
        ctx.state.save()

    return stats, reasons, stop_reason, clean


def print_summary(stats: Counter, reasons: Counter, stop_reason: str, dry_run: bool) -> None:
    say("")
    say("=" * 60)
    say("الملخص" + (" (معاينة: لم يُحفظ شيء)" if dry_run else ""))
    say("=" * 60)
    say(f"سبب التوقف  : {stop_reason}")
    say(f"فُحصت       : {stats['scanned']}")
    say(f"عُدّلت       : {stats['edited']}")
    if dry_run:
        say(f"كانت ستُعدَّل : {stats['would_edit']}")
    say(f"من الذاكرة  : {stats['cached']} صفحة سبق فحصها ولم تتغير")
    say(f"أخطاء       : {stats['errors']}")
    for reason, count in reasons.most_common():
        say(f"   - {describe(reason)}: {count}")


# ============================================================
# اختبارات محلية (بلا اتصال): python references_bot.py --selftest
# ============================================================

def run_selftests() -> int:
    import tempfile

    tre = build_template_regex({TARGET_TEMPLATE, *EXTRA_LIST_TEMPLATES})
    head = "مقدمة<ref>أ</ref>\n\n"
    tail = "\n== وصلات خارجية ==\n* س\n"
    cases = [
        # (الاسم, النص, يُعدَّل؟, السبب المتوقع)
        ("أساسي", head + "== مراجع ==\n<references />\n" + tail, True, "ok"),
        ("بلا مسافة", head + "== مراجع ==\n<references/>\n" + tail, True, "ok"),
        ("حروف كبيرة", head + "== مراجع ==\n<REFERENCES />\n" + tail, True, "ok"),
        ("تعليق قبل الوسم", head + "== مراجع ==\n<!-- ملاحظة -->\n\n<references />\n" + tail, True, "ok"),
        ("عنوان به تعليق", head + "== مراجع == <!-- x -->\n<references />\n" + tail, True, "ok"),
        ("آخر الصفحة", head + "== مراجع ==\n<references />", True, "ok"),
        ("وسم آخر داخل تعليق", head + "== مراجع ==\n<references />\n<!-- <references /> -->\n", True, "ok"),
        ("وسمان بسيطان", head + "== مراجع ==\n<references />\n== ملاحظات ==\n<references />\n", False, "multiple-tags"),
        ("وسم مع group", head + '== ملاحظات ==\n<references group="n" />\n== مراجع ==\n<references />\n', False, "multiple-tags"),
        ("وسم مفتوح ومغلق", head + "== مراجع ==\n<references></references>\n", False, "no-target"),
        ("{{مراجع}} موجود", head + "== مراجع ==\n{{مراجع}}\n<references />\n", False, "has-list-template"),
        ("{{مراجع|2}} موجود", head + "== مراجع ==\n{{مراجع|2}}\n== ب ==\n<references />\n", False, "has-list-template"),
        ("قوالب مراجع كثيرة", head + "== أ ==\n{{مراجع}}\n== ب ==\n{{مراجع}}\n== ج ==\n<references />\n", False, "has-list-template"),
        ("Reflist موجود", head + "== مراجع ==\n{{Reflist}}\n== ب ==\n<references />\n", False, "has-list-template"),
        ("مع بادئة قالب:", head + "== مراجع ==\n{{قالب:مراجع}}\n== ب ==\n<references />\n", False, "has-list-template"),
        ("قالب بمسافات", head + "== مراجع ==\n{{ مراجع }}\n== ب ==\n<references />\n", False, "has-list-template"),
        ("قالب بسطر جديد", head + "== مراجع ==\n{{مراجع\n|2}}\n== ب ==\n<references />\n", False, "has-list-template"),
        ("#tag:references", head + "== مراجع ==\n{{#tag:references}}\n== ب ==\n<references />\n", False, "has-list-template"),
        ("داخل تعليق فقط", "== مراجع ==\n<!-- <references /> -->\n", False, "no-target"),
        ("داخل nowiki فقط", "== مراجع ==\n<nowiki><references /></nowiki>\n", False, "no-target"),
        ("داخل pre فقط", "== مراجع ==\n<pre>\n<references />\n</pre>\n", False, "no-target"),
        ("تعليق غير مغلق", head + "== مراجع ==\n<references />\n<!-- بلا إغلاق", False, "ambiguous-markup"),
        ("nowiki غير مغلق", head + "== مراجع ==\n<references />\n<nowiki>", False, "ambiguous-markup"),
        ("داخل div", head + '== مراجع ==\n<div class="reflist">\n<references />\n</div>\n', False, "unbalanced-html"),
        ("div في نفس السطر", '== مراجع ==\n<div style="column-count:2"><references /></div>', False, "inline"),
        ("بداية/نهاية المراجع", "== مراجع ==\n{{بداية المراجع}}\n<references />\n{{نهاية المراجع}}", False, "not-after-heading"),
        ("بلا عنوان", "نص\n<references />\n", False, "not-after-heading"),
        ("نص قبل الوسم", "== مراجع ==\nنص\n<references />", False, "not-after-heading"),
        ("مسافة بادئة", "== مراجع ==\n <references />\n", False, "indented"),
        ("نص بعد الوسم", "== مراجع ==\n<references /> نص\n", False, "inline"),
        ("قائمة نقطية", "== مراجع ==\n* <references />", False, "inline"),
        ("داخل قالب", head + "{{صندوق|\n== مراجع ==\n<references />\n}}", False, "unbalanced-template"),
        ("داخل جدول", "{|\n|\n== مراجع ==\n<references />\n|}", False, "unbalanced-table"),
        ("إغلاق بعد الوسم", "== مراجع ==\n<references />\n</div>", False, "wrapper-after"),
        ("فارغة", "", False, "empty"),
    ]

    failures = 0
    for name, text, expect_ok, expect_reason in cases:
        result = analyze_text(text, tre)
        good = result.ok == expect_ok and result.reason == expect_reason
        if good and result.ok:
            start, end = result.span
            good = (
                result.new_text == text[:start] + NEW_TEXT + text[end:]
                and result.new_text.count("\n") == text.count("\n")
                and result.new_text.count(NEW_TEXT) == 1
            )
        if not good:
            failures += 1
        print(("نجح   " if good else "فشل   ") + name +
              ("" if good else f"  ← حصلنا على ({result.ok}, {result.reason})"))

    # الذاكرة الدائمة
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "state.json"
        state = State(path)
        state.mark("صفحة", 10, "multiple-tags")
        state.save()
        again = State(path)
        checks = [
            again.seen("صفحة", 10),
            not again.seen("صفحة", 11),
            not again.seen("أخرى", 10),
        ]
        again.checked["صفحة"]["ts"] = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        checks.append(not again.seen("صفحة", 10))
        good = all(checks)
        failures += 0 if good else 1
        print(("نجح   " if good else "فشل   ") + "الذاكرة الدائمة")

    # الوقت
    naive = datetime(2026, 1, 1, 12, 0)
    aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    good = as_utc(naive) == aware == as_utc(aware)
    failures += 0 if good else 1
    print(("نجح   " if good else "فشل   ") + "التعامل مع الوقت")

    total = len(cases) + 2
    print(f"\n{total - failures}/{total} اختبار نجح")
    return 0 if failures == 0 else 1


# ============================================================
# نقطة الدخول
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="بوت تلقائي وآمن لاستبدال <references /> بالقالب {{مراجع}}"
    )
    parser.add_argument("--dry-run", action="store_true", help="معاينة فقط دون أي حفظ")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"أقصى عدد تعديلات (0 = بلا حد، الافتراضي {DEFAULT_LIMIT})")
    parser.add_argument("--max-scan", type=int, default=DEFAULT_MAX_SCAN,
                        help=f"أقصى عدد صفحات تُفحص (0 = بلا حد، الافتراضي {DEFAULT_MAX_SCAN})")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"ثوانٍ بين كل تعديلين (الافتراضي {DEFAULT_DELAY})")
    parser.add_argument("--min-age", type=int, default=MIN_MINUTES_SINCE_LAST_EDIT,
                        help="أقل عدد دقائق منذ آخر تعديل على الصفحة")
    parser.add_argument("--shutoff-page", default=SHUTOFF_PAGE,
                        help='صفحة الإيقاف الطارئ ({user} = اسم الحساب)، "" لتعطيلها')
    parser.add_argument("--ignore-messages", action="store_true",
                        help="لا تتوقف عند وجود رسالة جديدة في نقاش الحساب")
    parser.add_argument("--allow-non-bot", action="store_true",
                        help="اسمح بالتعديل الفعلي بحساب بلا صلاحية بوت")
    parser.add_argument("--no-server-verify", action="store_true",
                        help="عطّل التحقق عبر expandtemplates (غير موصى به)")
    parser.add_argument("--state-file", default=str(STATE_FILE), help="ملف الذاكرة الدائمة")
    parser.add_argument("--details", action="store_true", help="اعرض سبب تخطي كل صفحة")
    parser.add_argument("--selftest", action="store_true", help="اختبارات محلية بلا اتصال")
    return parser


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--selftest" in argv:
        return run_selftests()
    if pywikibot is None:
        print("Pywikibot غير مثبت. ثبّته بالأمر: pip install pywikibot")
        return 2

    args = build_parser().parse_args(pywikibot.handle_args(argv))
    dry_run = args.dry_run or bool(getattr(pywikibot.config, "simulate", False))
    if dry_run:
        say("*** وضع المعاينة: لن يُحفظ أي شيء ***")

    try:
        site = pywikibot.Site(LANG, FAMILY)
        template_re = preflight(site, args, dry_run)
        if template_re is None:
            return 1

        shutoff = args.shutoff_page.format(user=site.user()) if args.shutoff_page else None
        guard = Guard(site, shutoff, ignore_messages=args.ignore_messages)
        problem = guard.check(force=True)
        if problem:
            warn(f"لن أبدأ: {problem}")
            return 1

        # اسم وسيط "بوت" يختلف باختلاف إصدار Pywikibot (bot منذ 9.3، وقبله botflag)
        bot_arg = "bot" if "bot" in inspect.signature(pywikibot.Page.save).parameters else "botflag"
        ctx = Context(
            site=site,
            template_re=template_re,
            state=State(Path(args.state_file)),
            guard=guard,
            dry_run=dry_run,
            delay=args.delay,
            min_age_minutes=args.min_age,
            verify_server=not args.no_server_verify,
            details=args.details,
            save_kwargs={
                "summary": EDIT_SUMMARY,
                "minor": True,
                "force": False,                    # احترم {{nobots}} حتى داخل save()
                "apply_cosmetic_changes": False,   # لا تغييرات إضافية غير مقصودة
                bot_arg: True,
            },
        )

        stats, reasons, stop_reason, clean = run(ctx, args.limit, args.max_scan)
        print_summary(stats, reasons, stop_reason, dry_run)
        return 0 if clean else 1

    except KeyboardInterrupt:
        say("\nأوقفه المستخدم.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
