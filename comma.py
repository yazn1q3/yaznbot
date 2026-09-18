import re
import time
import mwparserfromhell
import pywikibot


# ============================== الإعدادات ==================================

SITE_LANG = "ar"
SITE_FAMILY = "wikipedia"

# نطاق المقالات الرئيسي في ويكيبيديا
ARTICLE_NAMESPACE = 0

# فاصل زمني بعد كل محاولة حفظ
SLEEP_BETWEEN_EDITS = 5

EDIT_SUMMARY = (
    "بوت: استبدال الفاصلة الإنجليزية (,) بالفاصلة العربية (،)"
)


# ============================== الأحرف العربية ==============================

ARABIC_CHARS = (
    r"\u0600-\u06FF"
    r"\u0750-\u077F"
    r"\u08A0-\u08FF"
    r"\uFB50-\uFDFF"
    r"\uFE70-\uFEFF"
)

# نستبدل الفاصلة فقط إذا:
# 1. كان قبلها حرف عربي.
# 2. وبعدها مسافات اختيارية ثم حرف عربي أو رقم عربي/لاتيني.
#
# أمثلة:
# "نص, نص"          -> "نص، نص"
# "السعودية, 123"   -> "السعودية، 123"
# "Hello, world"    -> لا يتغير
# "Microsoft, Inc." -> لا يتغير
# "Python, Java"    -> لا يتغير
# "1,000"           -> لا يتغير

ARABIC_COMMA_PATTERN = re.compile(
    rf"(?<=[{ARABIC_CHARS}]),(?=\s*[{ARABIC_CHARS}0-9٠-٩])"
)


def replace_arabic_commas(text):
    """
    يستبدل الفاصلة الإنجليزية بالفاصلة العربية
    فقط عندما يكون السياق عربيًا.
    """
    return ARABIC_COMMA_PATTERN.sub("،", text)


# ============================== الوسوم المحمية ==============================

PROTECTED_TAGS = {
    "ref",
    "references",
    "code",
    "syntaxhighlight",
    "source",
    "pre",
    "nowiki",
    "math",
    "chem",
    "hiero",
    "score",
    "timeline",
    "graph",
    "mapframe",
    "maplink",
    "imagemap",
    "gallery",
    "templatestyles",
    "templatedata",
    "inputbox",
    "charinsert",
    "style",
    "script",
}


# ============================== قوالب قيد التحرير ============================

IN_USE_TEMPLATES = {
    "يحرر",
    "تحرر",
    "قيد التحرير",
    "تحت التحرير",
    "inuse",
    "in use",
}


# ============================== أدوات القوالب ===============================

def normalize_template_name(raw_name):
    """
    توحيد اسم القالب.
    """
    name = str(raw_name).strip().replace("_", " ")
    lname = name.lower()

    if name.startswith("قالب:"):
        name = name[len("قالب:"):].strip()

    elif lname.startswith("template:"):
        name = name[len("template:"):].strip()

    return name


def find_in_use_signal(wikicode):
    """
    يبحث عن قالب يدل على أن الصفحة قيد التحرير.
    """

    normalized_set = {
        name.lower()
        for name in IN_USE_TEMPLATES
    }

    for template in wikicode.filter_templates():

        raw_name = str(template.name).strip()

        normalized_name = normalize_template_name(
            raw_name
        ).lower()

        if normalized_name in normalized_set:
            return raw_name

    return None


# ============================== معالجة الويكي كود ============================

def process_wikicode(wikicode):
    """
    يستبدل الفاصلة الإنجليزية بالفاصلة العربية
    داخل النص العربي الظاهر فقط.

    لا يلمس:
    - النصوص الإنجليزية.
    - القوالب.
    - وسوم الحماية.
    - الروابط الداخلية.
    - الروابط الخارجية.
    - التعليقات المخفية.
    """

    changed = False

    for node in wikicode.nodes:

        # ========================== النص العادي ==========================

        if isinstance(
            node,
            mwparserfromhell.nodes.Text
        ):

            new_value = replace_arabic_commas(
                node.value
            )

            if new_value != node.value:

                node.value = new_value
                changed = True

        # ========================== الوسوم ==============================

        elif isinstance(
            node,
            mwparserfromhell.nodes.Tag
        ):

            tag_name = str(
                node.tag
            ).strip().lower()

            if tag_name in PROTECTED_TAGS:
                continue

            if node.contents is not None:

                if process_wikicode(
                    node.contents
                ):
                    changed = True

        # ========================== العناوين ============================

        elif isinstance(
            node,
            mwparserfromhell.nodes.Heading
        ):

            if process_wikicode(
                node.title
            ):
                changed = True

        # ========================== القوالب =============================

        elif isinstance(
            node,
            mwparserfromhell.nodes.Template
        ):

            continue

        # ========================== الروابط الداخلية =====================

        elif isinstance(
            node,
            mwparserfromhell.nodes.Wikilink
        ):

            continue

        # ========================== الروابط الخارجية =====================

        elif isinstance(
            node,
            mwparserfromhell.nodes.ExternalLink
        ):

            continue

        # ========================== التعليقات ============================

        elif isinstance(
            node,
            mwparserfromhell.nodes.Comment
        ):

            continue

    return changed


def build_new_text(wikicode):

    changed = process_wikicode(
        wikicode
    )

    return (
        str(wikicode),
        changed
    )


# ============================== فحص الاستبعاد ===============================

def get_exclusion_reason(page, wikicode):

    try:

        if not page.botMayEdit():

            return (
                "قالب استبعاد بوتات قياسي "
                "({{bots}}/{{nobots}}) أو "
                "قالب تحرير نشط معروف لدى Pywikibot"
            )

    except Exception as e:

        return (
            "تعذّر التحقق من صلاحية التعديل "
            f"عبر botMayEdit(): {e}"
        )

    template_name = find_in_use_signal(
        wikicode
    )

    if template_name:

        return (
            "وُجد قالب حالة تحرير نشطة: "
            "{{"
            + template_name
            + "}}"
        )

    return None


# ============================== اختيار مقالة عشوائية ========================

def get_random_article(site):

    attempts = 0

    while True:

        attempts += 1

        try:

            request = site.simple_request(
                action="query",
                generator="random",
                grnnamespace=ARTICLE_NAMESPACE,
                grnlimit=1,
            )

            data = request.submit()

            pages = (
                data
                .get("query", {})
                .get("pages", {})
            )

            title = None

            for page_data in pages.values():

                title = page_data.get(
                    "title"
                )

                if title:
                    break

            if not title:

                print(
                    "لم يتم الحصول على عنوان، إعادة المحاولة..."
                )

                continue

            print(
                f"\nالمحاولة {attempts}: {title}"
            )

            page = pywikibot.Page(
                site,
                title
            )

            if not page.exists():

                print(
                    "   الصفحة غير موجودة، تجاوز."
                )

                continue

            if page.isRedirectPage():

                print(
                    "   تحويلة، تجاوز."
                )

                continue

            if page.namespace() != ARTICLE_NAMESPACE:

                print(
                    "   ليست في نطاق المقالات، تجاوز."
                )

                continue

            try:

                text = page.text

            except Exception as e:

                print(
                    f"   تعذر القراءة: {e}"
                )

                continue

            wikicode = mwparserfromhell.parse(
                text
            )

            exclusion_reason = get_exclusion_reason(
                page,
                wikicode
            )

            if exclusion_reason:

                print(
                    "الصفحة مستبعدة، تجاوز."
                )

                continue

            new_text, changed = build_new_text(
                wikicode
            )

            if not changed or new_text == text:

                print(
                    "   ✓ لا يوجد تغيير عربي مناسب، تجاوز."
                )

                continue

            print(
                "\n"
                + "=" * 72
            )

            print(
                " تم العثور على مقالة مناسبة!"
            )

            print(
                f"المقالة: {title}"
            )

            print(
                f"عدد المحاولات: {attempts}"
            )

            print(
                "=" * 72
            )

            return title

        except KeyboardInterrupt:

            raise

        except Exception as e:

            print(
                f"خطأ أثناء البحث: {e}"
            )

            print(
                "سيتم الانتقال إلى محاولة جديدة..."
            )

            time.sleep(2)


# ============================== معالجة الصفحة ================================

def process_page(site, title):

    print(
        "\n"
        + "=" * 72
    )

    print(
        f"===== {title} ====="
    )

    print(
        "=" * 72
    )

    try:

        page = pywikibot.Page(
            site,
            title
        )

        if not page.exists():

            print(
                "الصفحة غير موجودة، تم تجاهلها."
            )

            return

        if page.isRedirectPage():

            print(
                "الصفحة تحويلة، تم تجاهلها."
            )

            return

        if page.namespace() != ARTICLE_NAMESPACE:

            print(
                "الصفحة ليست في نطاق المقالات، تم تجاهلها."
            )

            return

        try:

            old_text = page.text

        except Exception as e:

            print(
                f"تعذر قراءة الصفحة: {e}"
            )

            return

        wikicode = mwparserfromhell.parse(
            old_text
        )

        exclusion_reason = get_exclusion_reason(
            page,
            wikicode
        )

        if exclusion_reason:

            print(
                "🔐 الصفحة مستبعدة من تعديل البوت: "
                f"{exclusion_reason}"
            )

            return

        new_text, changed = build_new_text(
            wikicode
        )

        if not changed or old_text == new_text:

            print(
                "✓ لا توجد فواصل عربية تحتاج إلى تغيير."
            )

            return

        # ========================== عرض الفرق ==========================

        print(
            "\nسيتم تغيير الفواصل الموجودة في السياق العربي فقط."
        )

        print(
            "\n"
            + "=" * 72
        )

        print(
            "🔍 DIFF"
        )

        print(
            "=" * 72
        )

        pywikibot.showDiff(
            old_text,
            new_text
        )

        # ========================== تأكيد المستخدم =====================

        while True:

            try:

                answer = input(
                    "\nحفظ التعديل؟ [Y/N]: "
                ).strip().lower()

            except EOFError:

                print(
                    "\n⚠️ لم يتم توفير إدخال."
                )

                return

            if answer in ("y", "yes"):

                print(
                    "✓ تم اختيار الحفظ."
                )

                break

            if answer in ("n", "no", ""):

                print(
                    "✗ تم تجاهل التعديل."
                )

                return

            print(
                "⚠️ أدخل Y للحفظ أو N للتجاهل."
            )

        # ========================== إعادة القراءة ========================

        print(
            "\n🔄 إعادة قراءة الصفحة قبل الحفظ..."
        )

        try:

            latest_text = page.text

        except Exception as e:

            print(
                f"تعذر التحقق من آخر نسخة: {e}"
            )

            return

        # ========================== منع التعارض =========================

        if latest_text != old_text:

            print(
                "🛑 تغيرت الصفحة منذ القراءة الأولى."
            )

            print(
                "لن يتم الحفظ لتجنب الكتابة فوق تعديل جديد."
            )

            return

        print(
            "✓ الصفحة لم تتغير."
        )

        # ========================== إعادة فحص الاستبعاد ================

        latest_wikicode = mwparserfromhell.parse(
            latest_text
        )

        exclusion_reason = get_exclusion_reason(
            page,
            latest_wikicode
        )

        if exclusion_reason:

            print(
                "🔐 ظهرت حالة استبعاد قبل الحفظ:"
            )

            print(
                exclusion_reason
            )

            print(
                "🛑 تم إلغاء الحفظ."
            )

            return

        print(
            "✓ لا يوجد استبعاد جديد."
        )

        # ========================== الحفظ ===============================

        page.text = new_text

        print(
            "\n💾 محاولة حفظ التعديل..."
        )

        try:

            page.save(
                summary=EDIT_SUMMARY
            )

            print(
                "✅ تم الحفظ بنجاح."
            )

        except pywikibot.exceptions.LockedPageError:

            print(
                "🔒 الصفحة محمية، تم تجاهلها."
            )

        except pywikibot.exceptions.EditConflictError:

            print(
                "⚠️ حدث تعارض في التحرير، تم إلغاء الحفظ."
            )

        except pywikibot.exceptions.SpamblacklistError:

            print(
                "🚫 رفض ميدياويكي التعديل بسبب قائمة الروابط المحظورة."
            )

        except pywikibot.exceptions.TitleblacklistError:

            print(
                "🚫 رفض ميدياويكي التعديل بسبب قائمة حظر العناوين."
            )

        except pywikibot.exceptions.AbuseFilterDisallowedError as e:

            print(
                "🛑 منع مرشح إساءة الاستخدام هذا التعديل:"
            )

            print(e)

        except pywikibot.exceptions.CaptchaError:

            print(
                "🧩 طُلب حل اختبار كابتشا."
            )

        except pywikibot.exceptions.PageInUseError:

            print(
                "🔒 الصفحة قيد التحرير حاليًا."
            )

        except pywikibot.exceptions.OtherPageSaveError as e:

            print(
                "🚫 فشل الحفظ:"
            )

            print(e)

        except Exception as e:

            print(
                "❌ خطأ غير متوقع أثناء الحفظ:"
            )

            print(e)

        finally:

            print(
                f"⏳ الانتظار {SLEEP_BETWEEN_EDITS} ثوانٍ..."
            )

            time.sleep(
                SLEEP_BETWEEN_EDITS
            )

    except KeyboardInterrupt:

        raise

    except Exception as e:

        print(
            f"حدث خطأ أثناء معالجة «{title}»: {e}"
        )


# ============================== التشغيل المستمر ==============================

def main():

    site = pywikibot.Site(
        SITE_LANG,
        SITE_FAMILY
    )

    try:

        site.login()

    except Exception as e:

        print(
            f"تعذر تسجيل الدخول: {e}"
        )

        return

    print(
        "\n"
        + "=" * 72
    )

    print(
        "🤖 تشغيل البوت مع مراجعة Y/N"
    )

    print(
        "النطاق: 0 (نطاق المقالات)"
    )

    print(
        "سيتم تعديل الفواصل الموجودة في السياق العربي فقط."
    )

    print(
        "اضغط Ctrl+C لإيقاف البوت."
    )

    print(
        "=" * 72
    )

    try:

        while True:

            # البحث عن مقالة جديدة
            title = get_random_article(
                site
            )

            if not title:

                print(
                    "تعذر العثور على مقالة مناسبة."
                )

                time.sleep(5)

                continue

            # معالجة المقالة
            process_page(
                site,
                title
            )

            # بعد Y أو N يرجع هنا ويبحث عن مقالة جديدة
            print(
                "\n🔄 البحث عن مقالة عشوائية أخرى..."
            )

            time.sleep(2)

    except KeyboardInterrupt:

        print(
            "\n"
            + "=" * 72
        )

        print(
            "🛑 تم إيقاف البوت بواسطة المستخدم."
        )

        print(
            "=" * 72
        )

    except Exception as e:

        print(
            "\n❌ خطأ غير متوقع:"
        )

        print(e)


# ============================== بدء البرنامج ================================

if __name__ == "__main__":
    main()
