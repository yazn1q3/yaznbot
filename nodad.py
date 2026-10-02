import pywikibot
import re
import time

SITE = pywikibot.Site("ar", "wikipedia")
SITE.login()

CATEGORY = "تصنيف:يتيمة"

TEMPLATE = r"\{\{\s*يتيمة(?:\s*\|[^}]*)?\s*\}\}"


def count_mainspace_backlinks(page):
    count = 0

    for ref in page.getReferences(
        namespaces=[0],
        follow_redirects=False,
        only_template_inclusion=False,
    ):
        if ref.namespace() == 0:
            count += 1

            if count >= 3:
                break

    return count


def remove_orphan_template(text):
    return re.sub(
        TEMPLATE,
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )


category = pywikibot.Category(SITE, CATEGORY)

print("=" * 60)
print("بدء فحص تصنيف:يتيمة")
print("=" * 60)

for page in category.articles(namespaces=[0]):

    print("=" * 60)
    print(f"فحص الصفحة: {page.title()}")

    try:
        text = page.text

        if not re.search(TEMPLATE, text, re.IGNORECASE):
            print("لا يوجد قالب {{يتيمة}}، سيتم تجاوز الصفحة.")
            continue

        backlinks = count_mainspace_backlinks(page)

        print(
            f"عدد صفحات النطاق الرئيسي التي تشير إلى الصفحة: "
            f"{backlinks}"
        )

        if backlinks >= 3:

            new_text = remove_orphan_template(text)

            if new_text == text:
                print("لم يحدث أي تغيير في النص.")
                continue

            page.text = new_text

            page.save(
                summary="بوت: إزالة قالب {{يتيمة}}"
            )

            print("تمت إزالة قالب {{يتيمة}}.")

            time.sleep(5)

        else:
            print(
                "عدد الروابط أقل من ثلاثة، "
                "ولذلك لم تتم إزالة قالب {{يتيمة}}."
            )

    except Exception as e:
        print(f"حدث خطأ أثناء معالجة {page.title()}: {e}")

print("=" * 60)
print("انتهى الفحص.")
