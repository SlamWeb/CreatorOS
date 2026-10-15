import { expect, test } from "@playwright/test";
import { chatHref } from "../src/components/chatLinks";

test("extraction links require a host receipt and an exact 64-hex job route", () => {
  const origin = "http://127.0.0.1:8877";
  const path = `/skills?extraction=${"a".repeat(64)}`;
  const links = [{ url: path, label: "查看提炼草稿", source_tool: "extract_skill_from_images" }];
  expect(chatHref(path, links, origin)).toEqual({ kind: "internal", href: path });
  expect(chatHref(path, [], origin)).toBeNull();
  expect(chatHref(`https://example.org${path}`, links, origin)).toBeNull();
  for (const invalid of ["short", "a".repeat(63), "z".repeat(64), `${"a".repeat(64)}&extra=1`, `${"a".repeat(64)}#fake`]) {
    const badPath = `/skills?extraction=${invalid}`;
    expect(chatHref(badPath, [{ ...links[0], url: badPath }], origin)).toBeNull();
  }
});
