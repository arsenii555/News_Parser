"""Парсер новостей с сайта wagon-cargo.ru"""
import json
import logging
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://wagon-cargo.ru"
NEWS_URL = f"{BASE_URL}/news/"
MAX_PAGES = 50
DELAY_SEC = 0.05
TIMEOUT_SEC = 20
STATE_FILE = Path("wagon_cargo_state.json")
OUTPUT_XLSX = Path("wagon_cargo_news.xlsx")

NEWS_SOURCE = "wagon_cargo"
TASK = "rzd_efficiency"
DATE_FMT = "%d.%m.%Y"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}

COLUMN_ORDER = ["title", "news_text", "publish_date", "link", "news_source", "task"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("wagon_cargo_parser.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def load_state(path: Path) -> set:
    """Загружает состояние из файла по пути path."""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            known = set(data.get("known_urls", []))
            logger.info("Состояние загружено: %d известных URL", len(known))
            return known
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Повреждённый файл состояния (%s). Начинаем с нуля.", exc)
    return set()


def save_state(path: Path, known_urls: set) -> None:
    """Сохраняет состояние в файле по пути path."""
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds") + "Z",
        "total_known": len(known_urls),
        "known_urls": sorted(known_urls),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Состояние сохранено: %d URL -> %s", len(known_urls), path)


def fetch(url: str, session: requests.Session) -> Optional[BeautifulSoup]:
    """Делает http запрос и получает html код страницы."""
    try:
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT_SEC)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as exc:
        logger.error("Ошибка запроса %s: %s", url, exc)
        return None


def clean_text(raw: Optional[str]) -> Optional[str]:
    """Очищает строку для загрузки в БД: BOM, NBSP, лишние пробелы и переносы."""
    if raw is None:
        return None
    text = raw.replace("\u00a0", " ").replace("\u00ad", "")
    text = re.sub(r"[^\S\n\t ]+", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text if text else None


def parse_date(raw: Optional[str]) -> Optional[date]:
    """Парсит дату из формата dd.mm.yyyy."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), DATE_FMT).date()
    except ValueError:
        logger.warning("Не удалось распарсить дату: %r", raw)
        return None


def normalize_url(href: str) -> str:
    """Нормализует url. Приводит все ссылки к абсолютным."""
    return urljoin(BASE_URL, href.strip())


def parse_listing_page(soup: BeautifulSoup) -> list:
    """Возвращает список {title, link, publish_date_raw} с одной страницы листинга."""
    items = []
    for card in soup.select("div#news-list div.item"):
        a = card.select_one("a.name")
        date_tag = card.select_one("span.date")
        if not a:
            continue
        items.append({
            "title": clean_text(a.get_text(" ", strip=True)),
            "link": normalize_url(str(a["href"])),
            "publish_date_raw": date_tag.get_text(strip=True) if date_tag else None,
        })
    return items


def parse_article(soup: BeautifulSoup) -> dict:
    """Извлекает title, news_text, publish_date_raw со страницы новости."""
    res = {}

    inf = soup.select_one("div.big-new div.inf")

    h1 = soup.select_one("h1.news-detail-name")
    res["title"] = clean_text(h1.get_text(" ", strip=True)) if h1 else None

    date_tag = inf.select_one("span.date") if inf else None
    res["publish_date_raw"] = date_tag.get_text(strip=True) if date_tag else None

    p_tag = inf.find("p") if inf else None
    res["news_text"] = clean_text(p_tag.get_text("\n", strip=True)) if p_tag else None

    return res


def scrape(max_pages: int = MAX_PAGES, force_full: bool = False) -> pd.DataFrame:
    """Обходит страницы с новостями и собирает их в DataFrame."""
    known_urls = set() if force_full else load_state(STATE_FILE)
    new_records = []
    session = requests.Session()
    stop_flag = False

    for page_num in range(1, max_pages + 1):
        if stop_flag:
            break

        page_url = NEWS_URL if page_num == 1 else (
            NEWS_URL + "?" + urlencode({"MUL_MODE": "", "PAGEN_1": page_num})
        )

        logger.info("=== Листинг стр. %d/%d -> %s", page_num, max_pages, page_url)
        soup = fetch(page_url, session)
        if soup is None:
            logger.warning("Стр. %d пропущена.", page_num)
            continue

        cards = parse_listing_page(soup)
        if not cards:
            logger.info("Стр. %d: карточки не найдены. Завершение.", page_num)
            break

        logger.info("  Найдено карточек: %d", len(cards))

        for card in cards:
            link = card["link"]

            if link in known_urls:
                logger.info("  Новость уже известна => останавливаемся: %s", link)
                stop_flag = True
                break

            time.sleep(DELAY_SEC)
            art_soup = fetch(link, session)

            if art_soup is None:
                logger.warning("  x Не удалось загрузить: %s", link)
                record = {
                    "title": card["title"],
                    "news_text": None,
                    "publish_date": parse_date(card["publish_date_raw"]),
                    "link": link,
                }
            else:
                art = parse_article(art_soup)
                record = {
                    "title": art.get("title") or card["title"],
                    "news_text": art.get("news_text"),
                    "publish_date": parse_date(
                        art.get("publish_date_raw") or card.get("publish_date_raw")
                    ),
                    "link": link,
                }

            record["news_source"] = NEWS_SOURCE
            record["task"] = TASK
            new_records.append(record)
            logger.info("  OK [%s] %s", record["publish_date"] or "??", (record["title"] or "")[:80])

        time.sleep(DELAY_SEC)

    if not new_records:
        return pd.DataFrame(columns=COLUMN_ORDER)

    df = _build_dataframe(new_records)
    known_urls.update(df["link"].tolist())
    save_state(STATE_FILE, known_urls)
    logger.info("Итого собрано новых новостей: %d", len(df))
    return df


def _build_dataframe(records: list) -> pd.DataFrame:
    df = pd.DataFrame(records)

    for col in COLUMN_ORDER:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[COLUMN_ORDER].copy()

    for col in ["title", "news_text", "link", "news_source", "task"]:
        df[col] = df[col].astype("string").str.strip().replace("", pd.NA)
    df["publish_date"] = pd.to_datetime(df["publish_date"], errors="coerce").dt.date

    n = len(df)
    df = df.dropna(subset=["title", "link"])
    if len(df) < n:
        logger.warning("Удалено %d записей: пустой title или link", n - len(df))

    n = len(df)
    df = df.drop_duplicates(subset=["link"], keep="first")
    if len(df) < n:
        logger.warning("Удалено %d дублей по полю link", n - len(df))

    df = df.sort_values("publish_date", ascending=False, na_position="last").reset_index(drop=True)
    return df


def main(max_pages: int = MAX_PAGES, force_full: bool = False, export_excel: bool = True) -> pd.DataFrame:
    """Запускает парсер и возвращает DataFrame с новыми новостями."""
    logger.info("=" * 65)
    logger.info("Парсер wagon-cargo.ru | режим: %s | страниц: %d",
                "ПОЛНЫЙ" if force_full else "ИНКРЕМЕНТАЛЬНЫЙ", max_pages)
    logger.info("=" * 65)

    df = scrape(max_pages=max_pages, force_full=force_full)
    if export_excel and not df.empty:
        df.to_excel(OUTPUT_XLSX, index=False)
    return df


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description="Парсер новостей wagon-cargo.ru")
    cli.add_argument("--pages", type=int, default=MAX_PAGES,
                     help=f"Кол-во страниц листинга (по умолчанию {MAX_PAGES})")
    cli.add_argument("--full", action="store_true",
                     help="Принудительный полный сбор (игнорировать кэш)")
    cli.add_argument("--no-excel", action="store_true",
                     help="Не сохранять в Excel")
    args = cli.parse_args()

    result = main(max_pages=args.pages, force_full=args.full, export_excel=not args.no_excel)

    if not result.empty:
        logger.info("\nСобрано: %d новостей", len(result))
        logger.info(result[["title", "publish_date", "link"]].to_string(max_colwidth=70))
    else:
        logger.info("Новых новостей не найдено.")
