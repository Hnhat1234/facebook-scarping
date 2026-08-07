import asyncio
import json
from playwright.async_api import async_playwright, Page

# =============================================================================
# PHASE 1: DISCOVERY (Scroll & Collect URLs)
# =============================================================================
async def collect_post_urls(page: Page, scroll_count: int) -> set:
    """Scrolls the group feed and extracts only the post permalinks."""
    print("[*] PHASE 1: Discovering post URLs...")
    seen_urls = set()

    for step in range(scroll_count):
        # Find all timestamp links (these contain the /posts/ or /permalink/ URLs)
        link_elements = await page.locator('a[href*="/posts/"], a[href*="/permalink/"]').all()
        
        for link in link_elements:
            try:
                href = await link.get_attribute("href")
                if href:
                    # Clean tracking parameters like ?__cft__[0]=...
                    clean_url = href.split("?")[0]
                    if not clean_url.startswith("http"):
                        clean_url = f"https://www.facebook.com{clean_url}"
                    seen_urls.add(clean_url)
            except Exception:
                # Ignore detached elements during scroll
                continue
                
        print(f"    [Step {step + 1}/{scroll_count}] Found {len(seen_urls)} unique URLs so far...")
        
        # Scroll down to load more
        await page.evaluate("window.scrollBy(0, 1000)")
        await asyncio.sleep(3) # Wait for network requests

    return seen_urls


# =============================================================================
# PHASE 2: EXTRACTION (Visit URLs & Scrape Data)
# =============================================================================
async def extract_individual_post(page: Page, url: str) -> dict:
    """Navigates to a specific post URL and extracts its content stably."""
    print(f"[*] Scraping: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(3) # Wait for text hydration

        #Printing the raw HTML for debug
        raw_html = await page.content()
        with open("debug_html", "w", encoding = "utf-8") as f:
            f.write(raw_html)
        print(f"\nSuccessfully write raw HTML of {url}\n")
    except Exception as e:
        print(f"    [!] Failed to load page: {e}")
        return None
    # 1. Expand "Xem thêm" / "See more"
    see_more_btn = page.locator('div[role="button"]').filter(has_text="Xem thêm")
    if await see_more_btn.count() == 0:
        see_more_btn = page.locator('div[role="button"]').filter(has_text="See more")
    if await see_more_btn.count() > 0:
        try:
            await see_more_btn.first.click(timeout=3000)
            await asyncio.sleep(1)
        except Exception:
            pass

    # 2. Extract Author (scoped to target post)
    author_name = ""
    header_loc = page.locator("h2, h3").first
    if await header_loc.count() > 0:
        author_link = header_loc.locator('a[role="link"]').first
        if await author_link.count() > 0:
            author_name = (await author_link.text_content()).strip()

    # STEP 3: EXTRACT MAIN POST BODY (scoped to target post)
    # -------------------------------------------------------------------------
    full_text = ""
    story_message = page.locator('div[data-ad-rendering-role="story_message"]').nth(1)

    if await story_message.count() > 0:
        # ---------------------------------------------------------------------
        # PATTERN 1:
        # story_message -> div[data-ad-preview="message"] -> div -> div -> span -> div -> children (div -> div)
        # ---------------------------------------------------------------------
        p1_parent = story_message.locator(
            'div[data-ad-preview="message"] > div > div > span > div'
        ).first

        if await p1_parent.count() > 0:
            children = await p1_parent.locator('> div').all()
            lines = []
            for child in children:
                txt = (await child.text_content()).strip()
                if txt and txt not in lines:
                    lines.append(txt)
            if lines:
                full_text = "\n".join(lines)

        # ---------------------------------------------------------------------
        # PATTERN 2:
        # story_message -> div -> span -> children (div -> div -> span)
        # ---------------------------------------------------------------------
        if not full_text:
            p2_parent = story_message.locator('> div > span').first
            if await p2_parent.count() > 0:
                children = await p2_parent.locator('> div').all()
                lines = []
                for child in children:
                    txt = (await child.text_content()).strip()
                    if txt and txt not in lines:
                        lines.append(txt)
                if lines:
                    full_text = "\n".join(lines)

        # ---------------------------------------------------------------------
        # STORY_MESSAGE DIRECT FALLBACK
        # ---------------------------------------------------------------------
        if not full_text:
            full_text = (await story_message.text_content()).strip()

        # DIRECT TARGET POST TEXT — last resort within scoped container
        if not full_text:
            raw = (await target_post.text_content()).strip()
            # Filter out navigation junk
            lines = [l.strip() for l in raw.split("\n") if l.strip()]
            ui_junk = {"Like", "Reply", "Share", "Comment", "Thích", "Trả lời", "Chia sẻ", "Bình luận"}
            lines = [l for l in lines if l not in ui_junk and len(l) > 1]
            full_text = "\n".join(lines) if lines else ""

    # -------------------------------------------------------------------------
    # FALLBACK: Walk dir="auto" nodes ONLY within the target post
    # -------------------------------------------------------------------------
    if not full_text:
        full_text = await page.evaluate("""
            (el) => {
                const boundary = el.querySelector('[role="group"], [aria-label*="Comment" i], [aria-label*="Bình luận" i]');
                const dirNodes = el.querySelectorAll('div[dir="auto"], span[dir="auto"]');
                let lines = [];
                for (let node of dirNodes) {
                    if (boundary && (node === boundary || (boundary.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING))) break;
                    let txt = node.textContent.trim();
                    if (txt.length > 2 && !["Like","Reply","Share","Comment","Thích","Trả lời","Chia sẻ"].includes(txt)) {
                        lines.push(txt);
                    }
                }
                return lines.join('\\n');
            }
        """)

    # 4. Extract Images (scoped to target post only)
    image_urls = []
    imgs = await page.locator('img').all()
    for img in imgs:
        try:
            src = await img.get_attribute("src")
            if src and ("scontent" in src or "fbcdn" in src) \
                    and "static.xx.fbcdn.net" not in src \
                    and "p50x50" not in src \
                    and "s40x40" not in src:
                image_urls.append(src)
        except Exception:
            continue

    return {
        "author": author_name,
        "post_url": url,
        "full_text": full_text,
        "image_urls": list(set(image_urls)),
    }
# =============================================================================
# MAIN ORCHESTRATOR
# =============================================================================
async def scrape_facebook_group_two_step(group_url: str, scroll_count: int = 5):
    try:
        with open("cookies.json", "r") as f:
            cookies = json.load(f)
    except FileNotFoundError:
        print("[!] Error: 'cookies.json' file not found.")
        return

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True, args=["--disable-notifications"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:153.0) Gecko/20100101 Firefox/153.0",
            viewport={"width": 1280, "height": 900},
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        # PHASE 1
        print(f"[*] Navigating to feed: {group_url}")
        await page.goto(group_url, wait_until="domcontentloaded")
        await asyncio.sleep(5)
        
        post_urls = await collect_post_urls(page, scroll_count)
        print(f"\n[+] Phase 1 Complete. Found {len(post_urls)} posts to scrape.\n")

        # Block background post feed loading during Phase 2 extraction
        # This prevents Facebook from appending other group posts below the target
        await page.route("**/*graphql*", lambda route: route.abort()
            if route.request.url and ("Stories" in route.request.url or "CometUFI" in route.request.url or "RelevantFeed" in route.request.url)
            else route.continue_()
        )

        # PHASE 2
        scraped_data = []
        for index, url in enumerate(post_urls):
            print(f"[*] Processing {index + 1}/{len(post_urls)}...")
            data = await extract_individual_post(page, url)
            
            if data and data["full_text"]:
                scraped_data.append(data)
                
            # Random delay to mimic human reading and avoid rate limits
            await asyncio.sleep(3)
        # Save Data
        output_file = "result.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(scraped_data, f, ensure_ascii=False, indent=2)

        print(f"\n[+] Scraping complete! Saved {len(scraped_data)} posts to '{output_file}'.")
        await browser.close()
if __name__ == "__main__":
    TARGET_GROUP = "https://www.facebook.com/groups/thuducbadminton"
    asyncio.run(scrape_facebook_group_two_step(TARGET_GROUP, scroll_count=10))