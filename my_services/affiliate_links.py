import argparse
import os
from urllib.parse import quote_plus

from my_tools.db import get_db_connection

DEFAULT_AFFILIATE_TAG = "proskiro02-21"


def resolve_affiliate_tag(affiliate_tag=None):
    """Resolve affiliate tag from arg -> env -> default."""
    return affiliate_tag or os.getenv("AMAZON_AFFILIATE_TAG") or DEFAULT_AFFILIATE_TAG


def create_affiliate_link(isbn_10, isbn_13, title=None, affiliate_tag=None):
    """Generate Amazon affiliate search link.

    Uses ISBN-13 search (most reliable) until PA API access is available.
    """
    tag = resolve_affiliate_tag(affiliate_tag)

    # Prefer ISBN-13 search (most reliable)
    if isbn_13:
        return f"https://www.amazon.co.uk/s?k={isbn_13}&i=stripbooks&tag={tag}"

    # Fallback to ISBN-10 search
    if isbn_10:
        return f"https://www.amazon.co.uk/s?k={isbn_10}&i=stripbooks&tag={tag}"

    # Last resort: search by title
    if title:
        encoded_title = quote_plus(title)
        return f"https://www.amazon.co.uk/s?k={encoded_title}&i=stripbooks&tag={tag}"

    return None


def populate_affiliate_links(regenerate_all=False, affiliate_tag=None):
    """Generate and store affiliate links for all books.

    Args:
        regenerate_all: If True, regenerate all links. If False, only fill missing ones.
        affiliate_tag: Override affiliate tag to apply to generated links.
    """
    conn = get_db_connection()
    tag = resolve_affiliate_tag(affiliate_tag)

    with conn.cursor() as cur:
        if regenerate_all:
            cur.execute("""
                SELECT id, isbn_10, isbn_13, title 
                FROM books
            """)
        else:
            cur.execute("""
                SELECT id, isbn_10, isbn_13, title 
                FROM books 
                WHERE amazon_affiliate_url IS NULL
            """)
        books = cur.fetchall()

    print(f"Using affiliate tag: {tag}")
    print(f"Found {len(books)} books to process", flush=True)

    total = len(books)
    updated = 0
    for index, (book_id, isbn_10, isbn_13, title) in enumerate(books, start=1):
        affiliate_link = create_affiliate_link(
            isbn_10,
            isbn_13,
            title,
            affiliate_tag=tag,
        )

        if affiliate_link:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE books 
                    SET amazon_affiliate_url = %s 
                    WHERE id = %s
                """, (affiliate_link, book_id))
            updated += 1

        if index % 100 == 0 or index == total:
            conn.commit()
            print(f"Progress: {index}/{total} processed, {updated} updated", flush=True)

    conn.commit()
    conn.close()

    print(f"Updated {updated} books with affiliate links")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate/update Amazon affiliate links")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Regenerate links for every book (not just missing links).",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="Affiliate tag override (defaults to AMAZON_AFFILIATE_TAG env var, then internal default).",
    )
    args = parser.parse_args()

    if args.all:
        print("Regenerating ALL affiliate links...")

    populate_affiliate_links(regenerate_all=args.all, affiliate_tag=args.tag)