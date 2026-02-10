from my_tools.db import get_db_connection
from urllib.parse import quote_plus

AFFILIATE_TAG = "proskiro-21"


def create_affiliate_link(isbn_10, isbn_13, title=None):
    """Generate Amazon affiliate search link.
    
    Uses ISBN-13 search (most reliable) until PA API access is available.
    """
    # Prefer ISBN-13 search (most reliable)
    if isbn_13:
        return f"https://www.amazon.co.uk/s?k={isbn_13}&i=stripbooks&tag={AFFILIATE_TAG}"
    
    # Fallback to ISBN-10 search
    if isbn_10:
        return f"https://www.amazon.co.uk/s?k={isbn_10}&i=stripbooks&tag={AFFILIATE_TAG}"
    
    # Last resort: search by title
    if title:
        encoded_title = quote_plus(title)
        return f"https://www.amazon.co.uk/s?k={encoded_title}&i=stripbooks&tag={AFFILIATE_TAG}"
    
    return None

def populate_affiliate_links(regenerate_all=False):
    """Generate and store affiliate links for all books.
    
    Args:
        regenerate_all: If True, regenerate all links. If False, only fill missing ones.
    """
    conn = get_db_connection()
    
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
    
    print(f"Found {len(books)} books to process")
    
    updated = 0
    for book_id, isbn_10, isbn_13, title in books:
        affiliate_link = create_affiliate_link(isbn_10, isbn_13, title)
        
        if affiliate_link:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE books 
                    SET amazon_affiliate_url = %s 
                    WHERE id = %s
                """, (affiliate_link, book_id))
            updated += 1
    
    conn.commit()
    conn.close()
    
    print(f"Updated {updated} books with affiliate links")

if __name__ == "__main__":
    import sys
    regenerate = "--all" in sys.argv
    
    if regenerate:
        print("Regenerating ALL affiliate links...")
    
    populate_affiliate_links(regenerate_all=regenerate)