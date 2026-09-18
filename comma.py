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
    "بوت: استبدال الفاصلة الإنجليزية (,) بالفاصلة العربية (،) "
    "في النص العربي الظاهر فقط، دون تعديل بالقوالب أو الاستشهادات أو الوسوم التقنية"
)


# ============================== الأحرف العربية ==============================

# نطاقات Unicode الشائعة للأحرف العربية
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
# "نص, نص"       -> "نص، نص"
# "السعودية, 123" -> "السعودية، 123"
# "Hello, world" -> لا يتغير
# "Microsoft, Inc." -> لا يتغير
# "Python, Java" -> لا يتغير
# "1,000" -> لا يتغير

ARABIC_COMMA_PATTERN = re.compile(
    rf"(?<=[{ARABIC_CHARS}]),(?=\s*[{ARABIC_CHARS}0-9٠-٩])"
)


def replace_arabic_commas(text):
    """
    يستبدل الفاصلة الإنجليزية بالفاصلة العربية
    فقط عندما يكون السياق عربيًا.

    لا يغير:
    - النصوص الإنجليزية الخالصة.
    - أسماء الشركات والبرامج الإنجليزية.
    - الأرقام مثل 1,000.
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
    توحيد اسم القالب:
    - إزالة المسافات الزائدة.
    - تحويل الشرطة السفلية إلى مسافة.
    - إزالة بادئة قالب: أو Template: إن وجدت.
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
    يبحث في قوالب الصفحة، بما فيها القوالب المتداخلة،
    عن قالب يدل على أن الصفحة قيد التحرير.
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

            # حماية كاملة للوسوم التقنية
            if tag_name in PROTECTED_TAGS:
                continue

            # معالجة محتوى الوسم غير المحمي
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
            # لا نعدل داخل أي قالب
            continue

        # ========================== الروابط الداخلية =====================

        elif isinstance(
            node,
            mwparserfromhell.nodes.Wikilink
        ):
            # لا نعدل داخل الروابط الداخلية
            continue

        # ========================== الروابط الخارجية =====================

        elif isinstance(
            node,
            mwparserfromhell.nodes.ExternalLink
        ):
            # لا نعدل داخل الروابط الخارجية
            continue

        # ========================== التعليقات ============================

        elif isinstance(
            node,
            mwparserfromhell.nodes.Comment
        ):
            # لا نعدل التعليقات المخفية
            continue

    return changed


def build_new_text(wikicode):
    """
    يعالج شجرة الويكي كود ويعيد:
    (النص الجديد، هل حدث تغيير؟)
    """
    changed = process_wikicode(
        wikicode
    )

    return (
        str(wikicode),
        changed
    )


# ============================== فحص الاستبعاد ===============================

def get_exclusion_reason(page, wikicode):
    """
    يتحقق من استبعاد الصفحة من تعديل البوت.
    """

    # الفحص الرسمي من Pywikibot
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

    # الفحص اليدوي لقوالب التحرير
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
    """
    يبحث بشكل مستمر عن مقالة عشوائية تحتوي على فاصلة إنجليزية
    قابلة للتعديل في سياق عربي، خارج القوالب والاستشهادات
    والوسوم المحمية.

    النصوص الإنجليزية مثل:
        Hello, world
        Microsoft, Inc.
        Python, Java

    لن تعتبر تعديلات قابلة للتنفيذ.
    """

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
                f"المحاولة {attempts}: {title}"
            )

            page = pywikibot.Page(
                site,
                title
            )

            # ----------------------------------------------------------
            # فحص الصفحة
            # ----------------------------------------------------------

            if not page.exists():
                print(
                    "    الصفحة غير موجودة، تجاوز."
                )
                continue

            if page.isRedirectPage():
                print(
                    "    تحويلة، تجاوز."
                )
                continue

            if page.namespace() != ARTICLE_NAMESPACE:
                print(
                    "    ليست في نطاق المقالات، تجاوز."
                )
                continue

            # ----------------------------------------------------------
            # قراءة الصفحة
            # ----------------------------------------------------------

            try:
                text = page.text

            except Exception as e:
                print(
                    f"    تعذر القراءة: {e}"
                )
                continue

            # ----------------------------------------------------------
            # فحص الاستبعاد
            # ----------------------------------------------------------

            wikicode = mwparserfromhell.parse(
                text
            )

            exclusion_reason = get_exclusion_reason(
                page,
                wikicode
            )

            if exclusion_reason:
                print(
                    "   🔐 الصفحة مستبعدة، تجاوز."
                )
                continue

            # ----------------------------------------------------------
            # تجربة إنشاء التعديل
            # ----------------------------------------------------------

            new_text, changed = build_new_text(
                wikicode
            )

            if not changed or new_text == text:
                print(
                    "   ✓ لا يوجد تغيير عربي مناسب، تجاوز."
                )
                continue

            # ----------------------------------------------------------
            # وجدنا مقالة مناسبة
            # ----------------------------------------------------------

            print(
                "\n"
                + "=" * 72
            )

            print(
                "🎯 تم العثور على مقالة مناسبة!"
            )

            print(
                f"📄 المقالة: {title}"
            )

            print(
                f"🔢 عدد المحاولات: {attempts}"
            )

            print(
                "=" * 72
            )

            return title

        except KeyboardInterrupt:
            raise

        except Exception as e:
            print(
                f"    خطأ أثناء البحث: {e}"
            )

            print(
                "   🔄 سيتم الانتقال إلى محاولة جديدة..."
            )


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

        # --------------------------------------------------------------
        # التحقق من وجود الصفحة
        # --------------------------------------------------------------

        if not page.exists():
            print(
                "الصفحة غير موجودة، تم تجاهلها."
            )
            return

        # --------------------------------------------------------------
        # التحقق من التحويلة
        # --------------------------------------------------------------

        if page.isRedirectPage():
            print(
                "الصفحة تحويلة، تم تجاهلها."
            )
            return

        # --------------------------------------------------------------
        # التحقق من النطاق
        # --------------------------------------------------------------

        if page.namespace() != ARTICLE_NAMESPACE:
            print(
                "الصفحة ليست في نطاق المقالات، تم تجاهلها."
            )
            return

        # --------------------------------------------------------------
        # قراءة الصفحة
        # --------------------------------------------------------------

        try:
            old_text = page.text

        except Exception as e:
            print(
                f"تعذر قراءة الصفحة: {e}"
            )
            return

        # --------------------------------------------------------------
        # تحليل الويكي كود
        # --------------------------------------------------------------

        wikicode = mwparserfromhell.parse(
            old_text
        )

        # --------------------------------------------------------------
        # فحص الاستبعاد
        # --------------------------------------------------------------

        exclusion_reason = get_exclusion_reason(
            page,
            wikicode
        )

        if exclusion_reason:
            print(
                "🔐 الصفحة مستبعدة من تعديل البوت — "
                f"{exclusion_reason}"
            )
            return

        # --------------------------------------------------------------
        # إنشاء النص الجديد
        # --------------------------------------------------------------

        new_text, changed = build_new_text(
            wikicode
        )

        # --------------------------------------------------------------
        # لا يوجد تغيير
        # --------------------------------------------------------------

        if not changed or old_text == new_text:
            print(
                "✓ لا توجد فواصل عربية تحتاج إلى تغيير "
                "خارج القوالب والاستشهادات."
            )
            return

        # --------------------------------------------------------------
        # عرض الفرق
        # --------------------------------------------------------------

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

        # --------------------------------------------------------------
        # تأكيد المستخدم
        # --------------------------------------------------------------

        answer = input(
            "\nحفظ التعديل؟ [y/N]: "
        ).strip().lower()

        if answer != "y":
            print(
                "✗ تم تجاهل التعديل."
            )
            return

        # --------------------------------------------------------------
        # إعادة قراءة الصفحة
        # --------------------------------------------------------------

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

        # --------------------------------------------------------------
        # منع الكتابة فوق تعديل جديد
        # --------------------------------------------------------------

        if latest_text != old_text:
            print(
                "تغيرت الصفحة منذ القراءة الأولى."
            )

            print(
                "🛑 لن يتم الحفظ لتجنب الكتابة فوق تعديلات جديدة."
            )

            return

        print(
            "✓ الصفحة لم تتغير."
        )

        # --------------------------------------------------------------
        # إعادة فحص قوالب الاستبعاد
        # --------------------------------------------------------------

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

        # --------------------------------------------------------------
        # وضع النص الجديد
        # --------------------------------------------------------------

        page.text = new_text

        # --------------------------------------------------------------
        # الحفظ
        # --------------------------------------------------------------

        print(
            "\n💾 محاولة حفظ التعديل..."
        )

        try:
            page.save(
                summary=EDIT_SUMMARY
            )

            print(
                "✓ تم الحفظ بنجاح."
            )

        except pywikibot.exceptions.LockedPageError:
            print(
                "🔒 الصفحة محمية، تم تجاهلها."
            )

        except pywikibot.exceptions.EditConflictError:
            print(
                "حدث تعارض في التحرير، تم إلغاء الحفظ."
            )

        except pywikibot.exceptions.SpamblacklistError:
            print(
                "🚫 رفض ميدياويكي التعديل بسبب "
                "قائمة الروابط المحظورة."
            )

        except pywikibot.exceptions.TitleblacklistError:
            print(
                "🚫 رفض ميدياويكي التعديل بسبب "
                "قائمة حظر العناوين."
            )

        except pywikibot.exceptions.AbuseFilterDisallowedError as e:
            print(
                "🛑 منع مرشح إساءة الاستخدام "
                f"هذا التعديل: {e}"
            )

        except pywikibot.exceptions.CaptchaError:
            print(
                "🧩 طُلب حل اختبار كابتشا؛ "
                "تعذر إتمام الحفظ آليًا."
            )

        except pywikibot.exceptions.PageInUseError:
            print(
                "الصفحة مقفلة حاليًا بواسطة "
                "عملية تحرير أخرى."
            )

        except pywikibot.exceptions.OtherPageSaveError as e:
            print(
                "فشل الحفظ، وقد يكون السبب "
                "قيدًا من {{bots}}/{{nobots}} أو غيره:"
            )

            print(e)

        except Exception as e:
            print(
                "خطأ غير متوقع أثناء الحفظ: "
                f"{e}"
            )

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


# ============================== التشغيل ======================================

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
        "البحث المستمر عن مقالة عشوائية مناسبة"
    )

    print(
        "النطاق: 0 (نطاق المقالات)"
    )

    print(
        "سيتم تعديل الفواصل الموجودة في السياق العربي فقط."
    )

    print(
        "سيتم تجاهل النصوص الإنجليزية والقوالب والاستشهادات."
    )

    print(
        "اضغط Ctrl+C لإيقاف البحث."
    )

    print(
        "=" * 72
    )

    try:
        title = get_random_article(
            site
        )

        if not title:
            print(
                "تعذر العثور على مقالة مناسبة."
            )
            return

        process_page(
            site,
            title
        )

    except KeyboardInterrupt:
        print(
            "\nتم إيقاف البوت بواسطة المستخدم."
        )

    except Exception as e:
        print(
            f"\nخطأ غير متوقع: {e}"
        )

    print(
        "\n✓ انتهى التشغيل."
    )


if __name__ == "__main__":
    main()
