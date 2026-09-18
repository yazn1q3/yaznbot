import io
import time
from typing import Optional

import requests
import torch
from PIL import Image
from transformers import (
    AutoImageProcessor,
    AutoModelForImageClassification,
)
import mwparserfromhell
import pywikibot


# ============================== إعدادات ويكيبيديا ==============================

SITE_LANG = "ar"
SITE_FAMILY = "wikipedia"

SLEEP_BETWEEN_EDITS = 5

EDIT_SUMMARY = (
    "بوت: إضافة قالب اخفاء وسيط للمحتوى بعد فحص آلي ومراجعة بشرية للصورة"
)


# ============================== إعدادات نموذج Hugging Face ======================

MODEL_NAME = "Falconsai/nsfw_image_detection"

# إذا تجاوزت nsfw هذه القيمة، تُحال الصورة للمراجعة البشرية.
NSFW_REVIEW_THRESHOLD = 0.80

# عرض الصورة المصغرة التي يتم تنزيلها للفحص.
THUMBNAIL_WIDTH = 512

IMAGE_DOWNLOAD_TIMEOUT = 20


# ============================== قالب الإخفاء ===================================

HIDE_TEMPLATE = "إخفاء وسيط"

HIDE_IMAGE_PARAMETER = "صورة"
HIDE_WIDTH_PARAMETER = "عرض"
HIDE_CAPTION_PARAMETER = "تعليق"


# ============================== الحماية ========================================

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
}

IN_USE_TEMPLATES = {
    "يحرر",
    "تحرر",
    "قيد التحرير",
    "تحت التحرير",
    "inuse",
    "in use",
}


# ============================== نموذج Hugging Face ==============================

_processor = None
_model = None
_device = None


def load_model():
    """تحميل النموذج مرة واحدة فقط."""

    global _processor
    global _model
    global _device

    if _model is not None:
        return

    print("\n🤖 تحميل نموذج Hugging Face...")
    print(f"النموذج: {MODEL_NAME}")

    _device = torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
    )

    print(f"الجهاز: {_device}")

    _processor = AutoImageProcessor.from_pretrained(
        MODEL_NAME
    )

    _model = AutoModelForImageClassification.from_pretrained(
        MODEL_NAME
    )

    _model.to(_device)
    _model.eval()

    print("✓ تم تحميل النموذج.\n")


def classify_image(
    image: Image.Image,
) -> dict[str, float]:
    """تصنيف الصورة وإرجاع احتمالات التصنيفات."""

    load_model()

    if image.mode != "RGB":
        image = image.convert("RGB")

    inputs = _processor(
        images=image,
        return_tensors="pt",
    )

    inputs = {
        key: value.to(_device)
        for key, value in inputs.items()
    }

    with torch.no_grad():
        outputs = _model(**inputs)

    probabilities = torch.softmax(
        outputs.logits,
        dim=-1,
    )[0]

    result = {}

    for index, probability in enumerate(
        probabilities
    ):
        label = str(
            _model.config.id2label[index]
        ).lower()

        result[label] = float(
            probability.item()
        )

    return result


# ============================== Wikimedia API ===================================

def download_wikimedia_image(
    site,
    file_title: str,
):
    """
    تنزيل نسخة مصغرة من صورة Wikimedia.

    تُرجع:
        (PIL.Image, image_url)

    أو:
        (None, None)
    """

    try:
        api_url = (
            f"{site.protocol()}://"
            f"{site.hostname()}/w/api.php"
        )

        params = {
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "titles": file_title,
            "iiprop": "url",
            "iiurlwidth": THUMBNAIL_WIDTH,
        }

        headers = {
            "User-Agent": (
                "YaznalbalawiBot/1.0 "
                "(https://ar.wikipedia.org/wiki/User:YaznalbalawiBot)"
            )
        }

        response = requests.get(
            api_url,
            params=params,
            headers=headers,
            timeout=IMAGE_DOWNLOAD_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        pages = (
            data.get("query", {})
            .get("pages", {})
        )

        for page_data in pages.values():

            imageinfo = page_data.get(
                "imageinfo"
            )

            if not imageinfo:
                print(
                    " لم يتم العثور على معلومات الصورة."
                )
                return None, None

            image_url = (
                imageinfo[0].get("thumburl")
                or imageinfo[0].get("url")
            )

            if not image_url:
                print(
                    " لم يتم العثور على رابط الصورة."
                )
                return None, None

            image_response = requests.get(
                image_url,
                headers=headers,
                timeout=IMAGE_DOWNLOAD_TIMEOUT,
            )

            image_response.raise_for_status()

            image = Image.open(
                io.BytesIO(
                    image_response.content
                )
            ).convert("RGB")

            return image, image_url

        print(
            " لم يتم العثور على الصفحة."
        )

        return None, None

    except Exception as error:
        print(
            f" تعذر تنزيل الصورة: {error}"
        )

        return None, None


# ============================== استخراج الملفات ================================

def normalize_file_title(
    title: str,
) -> str:
    """تحويل نطاقات ملف/صورة إلى File:."""

    title = title.strip()

    prefixes = (
        ("ملف:", "File:"),
        ("صورة:", "File:"),
        ("file:", "File:"),
        ("image:", "File:"),
    )

    for prefix, replacement in prefixes:

        if title.lower().startswith(
            prefix.lower()
        ):
            return (
                replacement
                + title[len(prefix):]
            )

    return title


def extract_image_files(
    wikicode,
) -> list[str]:
    """استخراج روابط ملفات Wikimedia من الويكيكود."""

    files = []

    for node in wikicode.nodes:

        if isinstance(
            node,
            mwparserfromhell.nodes.Wikilink,
        ):

            title = str(
                node.title
            ).strip()

            lower_title = title.lower()

            if lower_title.startswith(
                (
                    "ملف:",
                    "صورة:",
                    "file:",
                    "image:",
                )
            ):

                files.append(
                    normalize_file_title(
                        title
                    )
                )

        elif isinstance(
            node,
            mwparserfromhell.nodes.Tag,
        ):

            tag_name = str(
                node.tag
            ).strip().lower()

            if tag_name in PROTECTED_TAGS:
                continue

            if node.contents is not None:

                files.extend(
                    extract_image_files(
                        node.contents
                    )
                )

        elif isinstance(
            node,
            mwparserfromhell.nodes.Heading,
        ):

            files.extend(
                extract_image_files(
                    node.title
                )
            )

        elif isinstance(
            node,
            (
                mwparserfromhell.nodes.Template,
                mwparserfromhell.nodes.Comment,
            ),
        ):
            continue

    return files


# ============================== حماية الصفحة ===================================

def normalize_template_name(
    raw_name: str,
) -> str:

    name = (
        str(raw_name)
        .strip()
        .replace("_", " ")
    )

    lower_name = name.lower()

    if name.startswith("قالب:"):

        name = name[
            len("قالب:"):
        ].strip()

    elif lower_name.startswith(
        "template:"
    ):

        name = name[
            len("template:"):
        ].strip()

    return name


def find_in_use_signal(
    wikicode,
) -> Optional[str]:

    normalized = {
        name.lower()
        for name in IN_USE_TEMPLATES
    }

    for template in wikicode.filter_templates():

        raw_name = str(
            template.name
        ).strip()

        normalized_name = (
            normalize_template_name(
                raw_name
            ).lower()
        )

        if normalized_name in normalized:
            return raw_name

    return None


def get_exclusion_reason(
    page,
    wikicode,
) -> Optional[str]:

    try:

        if not page.botMayEdit():

            return (
                "botMayEdit() رفض التعديل بسبب "
                "قالب استبعاد بوتات أو حالة تحرير."
            )

    except Exception as error:

        return (
            "تعذر التحقق من botMayEdit(): "
            f"{error}"
        )

    template_name = find_in_use_signal(
        wikicode
    )

    if template_name:

        return (
            "وُجد قالب حالة تحرير نشطة: "
            f"{{{{{template_name}}}}}"
        )

    return None


# ============================== أدوات الصور ====================================

def get_image_name(
    node,
) -> str:
    """
    استخراج اسم الملف فقط.

    مثال:
        [[ملف:Example.jpg|تصغير]]

    النتيجة:
        Example.jpg
    """

    file_title = normalize_file_title(
        str(node.title).strip()
    )

    if file_title.lower().startswith(
        "file:"
    ):
        return file_title[5:].strip()

    return file_title

def get_image_options(node):
    """
    يستخرج عرض الصورة والتعليق من Wikilink.

    مثال:
    [[ملف:Example.jpg|تصغير|تعليق الصورة]]

    يعيد:
    ("250 بك", "تعليق الصورة")
    """
    width = ""
    caption = ""

    # node.text هو الجزء الموجود بعد اسم الملف.
    # مثال:
    # [[ملف:Example.jpg|تصغير|تعليق]]
    # node.text = "تصغير|تعليق"
    text = str(node.text)

    if not text:
        return width, caption

    parts = text.split("|")

    for part in parts:
        value = part.strip()

        if not value:
            continue

        # خيار التصغير
        if value.lower() in {
            "تصغير",
            "thumb",
            "thumbnail",
        }:
            width = "250 بك"
            continue

        # عرض محدد مثل:
        # 250 بك
        # 250px
        if "بك" in value or "px" in value.lower():
            width = value
            continue

        # خيارات لا تمثل تعليقًا
        if value.lower() in {
            "إطار",
            "frameless",
            "frame",
            "border",
            "يمين",
            "يسار",
            "وسط",
            "center",
            "left",
            "right",
            "none",
        }:
            continue

        # أول نص متبقٍ نعتبره تعليق الصورة
        if not caption:
            caption = value

    return width, caption


# ============================== قالب الإخفاء ===================================

def build_hide_template(
    image_name: str,
    width: str = "",
    caption: str = "",
) -> str:
    """
    إنشاء قالب إخفاء الوسيط.

    مثال:

    {{إخفاء وسيط
    |صورة= Example.jpg
    |عرض= 250 بك
    |تعليق= وصف
    }}
    """

    return (
        "{{"
        + HIDE_TEMPLATE
        + "\n|"
        + HIDE_IMAGE_PARAMETER
        + "= "
        + image_name
        + "\n|"
        + HIDE_WIDTH_PARAMETER
        + "= "
        + width
        + "\n|"
        + HIDE_CAPTION_PARAMETER
        + "= "
        + caption
        + "\n}}"
    )


# ============================== إضافة القالب ===================================

def add_hide_template(
    wikicode,
    approved_files: set[str],
) -> bool:
    """
    استبدال روابط الصور التي وافق عليها المستخدم
    بقالب إخفاء الوسيط.

    مثال:

    [[ملف:Example.jpg|تصغير]]

    تصبح:

    {{إخفاء وسيط
    |صورة= Example.jpg
    |عرض= 250 بك
    |تعليق=
    }}

    لا يتم لمس القوالب أو المناطق المحمية.
    """

    changed = False

    for node in list(wikicode.nodes):

        # =====================================================
        # صورة Wikimedia
        # =====================================================

        if isinstance(
            node,
            mwparserfromhell.nodes.Wikilink,
        ):

            file_title = normalize_file_title(
                str(node.title).strip()
            )

            if (
                file_title.lower()
                not in approved_files
            ):
                continue

            # استخراج اسم الملف فقط.
            image_name = get_image_name(
                node
            )

            # استخراج العرض والتعليق.
            width, caption = (
                get_image_options(
                    node
                )
            )

            replacement_text = (
                build_hide_template(
                    image_name=image_name,
                    width=width,
                    caption=caption,
                )
            )

            replacement = (
                mwparserfromhell.parse(
                    replacement_text
                )
            )

            wikicode.replace(
                node,
                replacement,
            )

            changed = True

        # =====================================================
        # وسوم HTML غير المحمية
        # =====================================================

        elif isinstance(
            node,
            mwparserfromhell.nodes.Tag,
        ):

            tag_name = str(
                node.tag
            ).strip().lower()

            if tag_name in PROTECTED_TAGS:
                continue

            if node.contents is not None:

                if add_hide_template(
                    node.contents,
                    approved_files,
                ):

                    changed = True

        # =====================================================
        # العناوين
        # =====================================================

        elif isinstance(
            node,
            mwparserfromhell.nodes.Heading,
        ):

            if add_hide_template(
                node.title,
                approved_files,
            ):

                changed = True

        # =====================================================
        # القوالب والتعليقات
        # =====================================================

        elif isinstance(
            node,
            (
                mwparserfromhell.nodes.Template,
                mwparserfromhell.nodes.Comment,
            ),
        ):
            continue

    return changed


# ============================== المراجعة البشرية ===============================

def review_images(
    site,
    old_text: str,
) -> set[str]:
    """
    الفحص الآلي ثم المراجعة البشرية.

    لا يتم الحفظ هنا.
    """

    wikicode = mwparserfromhell.parse(
        old_text
    )

    files = extract_image_files(
        wikicode
    )

    unique_files = []
    seen = set()

    for file_title in files:

        key = file_title.lower()

        if key in seen:
            continue

        seen.add(key)

        unique_files.append(
            file_title
        )

    if not unique_files:

        print(
            "🖼️ لا توجد صور Wikimedia في الصفحة."
        )

        return set()

    print(
        f"\n🖼️ عدد الصور الفريدة: "
        f"{len(unique_files)}"
    )

    approved = set()

    for number, file_title in enumerate(
        unique_files,
        start=1,
    ):

        print(
            "\n"
            + "=" * 72
        )

        print(
            f"الصورة {number}/"
            f"{len(unique_files)}"
        )

        print(
            f"الملف: {file_title}"
        )

        try:

            image, image_url = (
                download_wikimedia_image(
                    site,
                    file_title,
                )
            )

            if image is None:

                print(
                    " تعذر تنزيل الصورة."
                )

                continue

            print(
                f"🔗 {image_url}"
            )

            print(
                "🤖 يجري التصنيف..."
            )

            scores = classify_image(
                image
            )

            nsfw_score = scores.get(
                "nsfw",
                0.0,
            )

            normal_score = scores.get(
                "normal",
                0.0,
            )

            print(
                f"normal: "
                f"{normal_score:.2%}"
            )

            print(
                f"nsfw:   "
                f"{nsfw_score:.2%}"
            )

            # =================================================
            # العتبة ليست قرارًا نهائيًا
            # =================================================

            if (
                nsfw_score
                < NSFW_REVIEW_THRESHOLD
            ):

                print(
                    "✓ لم تتجاوز الصورة "
                    "عتبة المراجعة."
                )

                answer = input(
                    "هل تريد مراجعتها يدويًا "
                    "رغم ذلك؟ [y/N]: "
                ).strip().lower()

                if answer != "y":
                    continue

            else:

                print(
                    " أُحيلت الصورة "
                    "إلى المراجعة البشرية."
                )

            # =================================================
            # القرار البشري
            # =================================================

            print(
                "\n👤 القرار النهائي بشري."
            )

            print(
                "افتح رابط الصورة وراجعها "
                "وفق سياسات ويكيبيديا."
            )

            print(
                f"🔗 {image_url}"
            )

            answer = input(
                "هل تعتمد هذه الصورة "
                "لإضافة القالب؟ [y/N]: "
            ).strip().lower()

            if answer != "y":

                print(
                    "تم تجاوز الصورة."
                )

                continue

            approved.add(
                file_title.lower()
            )

            print(
                "✓ تمت الموافقة على هذه الصورة."
            )

        except requests.RequestException as error:

            print(
                " خطأ في تنزيل الصورة: "
                f"{error}"
            )

        except Exception as error:

            print(
                " خطأ أثناء الفحص: "
                f"{error}"
            )

    return approved


# ============================== معالجة الصفحة ==================================

def process_page(
    site,
    title: str,
):

    print(
        "\n"
        + "=" * 72
    )

    print(
        f"===== {title} ====="
    )

    page = pywikibot.Page(
        site,
        title,
    )

    if not page.exists():

        print(
            " الصفحة غير موجودة."
        )

        return

    if page.isRedirectPage():

        print(
            " الصفحة تحويلة، تم تجاهلها."
        )

        return

    # =========================================================
    # القراءة الأولى
    # =========================================================

    try:

        old_text = page.text

    except Exception as error:

        print(
            " تعذر قراءة الصفحة: "
            f"{error}"
        )

        return

    wikicode = mwparserfromhell.parse(
        old_text
    )

    # =========================================================
    # فحص الاستبعاد
    # =========================================================

    exclusion = get_exclusion_reason(
        page,
        wikicode,
    )

    if exclusion:

        print(
            f"🔐 الصفحة مستبعدة: "
            f"{exclusion}"
        )

        return

    # =========================================================
    # الفحص الآلي + المراجعة البشرية
    # =========================================================

    approved_files = review_images(
        site,
        old_text,
    )

    if not approved_files:

        print(
            "\n✓ لم يتم اعتماد أي صورة للتعديل."
        )

        return

    # =========================================================
    # إنشاء النص الجديد
    # =========================================================

    new_wikicode = mwparserfromhell.parse(
        old_text
    )

    changed = add_hide_template(
        new_wikicode,
        approved_files,
    )

    if not changed:

        print(
            "✓ لا يوجد تعديل جديد لإجرائه."
        )

        return

    new_text = str(
        new_wikicode
    )

    # =========================================================
    # عرض Diff
    # =========================================================

    print(
        "\n📝 التعديل المقترح:"
    )

    pywikibot.showDiff(
        old_text,
        new_text,
    )

    answer = input(
        "\nهل تريد الانتقال إلى "
        "فحص ما قبل الحفظ؟ [y/N]: "
    ).strip().lower()

    if answer != "y":

        print(
            "🛑 تم إلغاء التعديل."
        )

        return

    # =========================================================
    # إعادة قراءة الصفحة
    # =========================================================

    try:

        latest_text = page.text

    except Exception as error:

        print(
            " تعذر إعادة قراءة الصفحة: "
            f"{error}"
        )

        return

    if latest_text != old_text:

        print(
            " تغيرت الصفحة منذ القراءة الأولى."
        )

        print(
            "لن يتم الحفظ لتجنب تعارض التحرير."
        )

        return

    # =========================================================
    # فحص الحماية مرة أخرى
    # =========================================================

    latest_wikicode = (
        mwparserfromhell.parse(
            latest_text
        )
    )

    exclusion = get_exclusion_reason(
        page,
        latest_wikicode,
    )

    if exclusion:

        print(
            "🔐 ظهرت حالة استبعاد قبل الحفظ:"
        )

        print(
            exclusion
        )

        print(
            "🛑 تم إلغاء الحفظ."
        )

        return

    # =========================================================
    # التأكيد النهائي
    # =========================================================

    print(
        "\n"
        + "=" * 72
    )

    print(
        "👤 المراجعة البشرية النهائية"
    )

    print(
        "سيتم حفظ التعديلات التي "
        "وافقت عليها فقط."
    )

    print(
        "\nاكتب YES حرفيًا "
        "لتأكيد الحفظ النهائي."
    )

    final_answer = input(
        "تأكيد الحفظ: "
    ).strip()

    if final_answer != "YES":

        print(
            "🛑 لم يتم الحفظ."
        )

        return

    # =========================================================
    # الحفظ
    # =========================================================

    page.text = new_text

    try:

        page.save(
            summary=EDIT_SUMMARY,
        )

        print(
            "✓ تم الحفظ بنجاح."
        )

    except pywikibot.exceptions.LockedPageError:

        print(
            "🔒 الصفحة محمية."
        )

    except pywikibot.exceptions.EditConflictError:

        print(
            " حدث تعارض في التحرير."
        )

    except pywikibot.exceptions.SpamblacklistError:

        print(
            "🚫 رفض التعديل بسبب Spam blacklist."
        )

    except pywikibot.exceptions.TitleblacklistError:

        print(
            "🚫 رفض التعديل بسبب Title blacklist."
        )

    except pywikibot.exceptions.AbuseFilterDisallowedError as error:

        print(
            "🛑 منعه مرشح إساءة الاستخدام: "
            f"{error}"
        )

    except pywikibot.exceptions.CaptchaError:

        print(
            "🧩 طُلب CAPTCHA."
        )

    except pywikibot.exceptions.PageInUseError:

        print(
            "  الصفحة قيد التحرير حاليًا."
        )

    except pywikibot.exceptions.OtherPageSaveError as error:

        print(
            f" فشل الحفظ: {error}"
        )

    except Exception as error:

        print(
            f"  خطأ غير متوقع: {error}"
        )

    finally:

        time.sleep(
            SLEEP_BETWEEN_EDITS
        )


# ============================== main ============================================

# ============================== اختيار مقالة عشوائية ==============================

def get_random_article(site):
    api_url = (
        f"{site.protocol()}://"
        f"{site.hostname()}/w/api.php"
    )

    params = {
        "action": "query",
        "format": "json",
        "generator": "random",
        "grnnamespace": 0,
        "grnlimit": 1,
    }

    headers = {
        "User-Agent": (
            "YaznalbalawiBot/1.0 "
            "(https://ar.wikipedia.org/wiki/User:YaznalbalawiBot)"
        )
    }

    response = requests.get(
        api_url,
        params=params,
        headers=headers,
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    pages = (
        data
        .get("query", {})
        .get("pages", {})
    )

    for page_data in pages.values():
        title = page_data.get("title")

        if title:
            return title

    return None


# ============================== main ============================================

def main():
    site = pywikibot.Site(
        SITE_LANG,
        SITE_FAMILY,
    )

    try:
        site.login()
    except Exception as error:
        print(
            "تعذر تسجيل الدخول: "
            f"{error}"
        )
        return

    for attempt in range(1, 101):
        try:
            title = get_random_article(site)

            if not title:
                print(
                    "لم يتم العثور على مقالة عشوائية."
                )
                continue

            print(
                "\n"
                + "=" * 72
            )
            print(
                f"المقالة العشوائية "
                f"({attempt}/100): {title}"
            )
            print(
                "=" * 72
            )

            process_page(
                site,
                title,
            )

            time.sleep(
                SLEEP_BETWEEN_EDITS
            )

        except KeyboardInterrupt:
            print(
                "\nتم إيقاف البوت بواسطة المستخدم."
            )
            break

        except Exception as error:
            print(
                "خطأ أثناء معالجة "
                f"{title if 'title' in locals() else 'المقالة'}: "
                f"{error}"
            )

            time.sleep(
                SLEEP_BETWEEN_EDITS
            )


if __name__ == "__main__":
    main()