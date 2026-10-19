"""Browser behavior that unit tests cannot reach: the page script."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.visual


def test_collapsed_section_stays_collapsed_after_a_live_update(
    app: Callable[..., str], page_for: Callable[..., Page]
) -> None:
    page = page_for()
    page.goto(app())
    replies = page.locator('details[data-section="replies"]')
    replies.locator("summary").click()
    expect(replies).not_to_have_attribute("open", "")
    page.locator('details[data-section="new"] button', has_text="Seen").first.click()
    expect(page.locator('details[data-section="new"] tbody tr')).to_have_count(2)
    expect(page.locator('details[data-section="replies"]')).not_to_have_attribute("open", "")


def test_text_size_buttons_and_shortcuts(app: Callable[..., str], page_for: Callable[..., Page]) -> None:
    page = page_for()
    page.goto(app())

    def zoom() -> str:
        return str(page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--zoom').trim()"))

    page.get_by_role("button", name="A+ Larger text").click()
    page.wait_for_function("getComputedStyle(document.documentElement).getPropertyValue('--zoom').trim() === '1.1'")
    assert zoom() == "1.1"
    page.keyboard.press("Control+0")
    page.wait_for_function("getComputedStyle(document.documentElement).getPropertyValue('--zoom').trim() === '1'")
    assert zoom() == "1"


def test_opening_an_item_removes_it_from_new(app: Callable[..., str], page_for: Callable[..., Page]) -> None:
    page = page_for()
    page.goto(app())
    rows = page.locator('details[data-section="new"] tbody tr')
    before = rows.count()
    with page.context.expect_page():
        page.get_by_role("link", name="#10412").click()
    expect(rows).to_have_count(before - 1)


def test_focus_moves_to_the_next_row_after_seen(app: Callable[..., str], page_for: Callable[..., Page]) -> None:
    page = page_for()
    page.goto(app())
    buttons = page.locator('details[data-section="new"] [data-action="seen"]')
    second = buttons.nth(1).get_attribute("data-key")
    buttons.first.focus()
    page.keyboard.press("Enter")
    expect(buttons).to_have_count(2)
    expect(page.locator(":focus")).to_have_attribute("data-key", second or "")
    expect(page.locator(":focus")).to_have_attribute("data-action", "seen")


def test_focus_stays_on_a_link_after_a_live_update(app: Callable[..., str], page_for: Callable[..., Page]) -> None:
    page = page_for()
    page.goto(app())
    link = page.get_by_role("link", name="#12", exact=True)
    link.focus()
    page.evaluate("htmx.trigger(document.body, 'repoboard-updated')")
    page.wait_for_timeout(500)
    expect(page.locator(":focus")).to_have_text("#12")
