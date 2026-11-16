import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MarkdownView } from "./MarkdownView";

const REQ = { id: "REQ-F-001", label: "Search by order number.", file: "02-specification/requirements.yaml", line: 4 };

describe("MarkdownView", () => {
  it("never runs markup from a file, and links identifiers and Intent files", async () => {
    const user = userEvent.setup();
    const onOpenId = vi.fn();
    const onOpenPath = vi.fn();
    const text = [
      "# Brief",
      "",
      "Delivers REQ-F-001, not REQ-F-999.",
      "",
      "See [the spec](../02-specification/requirements.yaml) and [the web](https://example.com).",
      "",
      '<script>window.__ran = true</script><img src=x onerror="window.__ran = true">',
      "",
      "`REQ-F-001` in code stays code.",
    ].join("\n");
    const { container } = render(
      <MarkdownView text={text} path="01-brief/brief.md" identifiers={[REQ]} onOpenId={onOpenId} onOpenPath={onOpenPath} />,
    );
    expect(container.querySelector("script, img[onerror]")).toBeNull();
    expect((window as unknown as { __ran?: boolean }).__ran).toBeUndefined();

    const links = screen.getAllByRole("button", { name: "REQ-F-001" });
    expect(links).toHaveLength(1); // the one in code is left alone, and an unknown ID is plain text
    expect(screen.queryByRole("button", { name: "REQ-F-999" })).toBeNull();
    await user.click(links[0]!);
    expect(onOpenId).toHaveBeenCalledWith(REQ);

    await user.click(screen.getByRole("link", { name: "the spec" }));
    expect(onOpenPath).toHaveBeenCalledWith("02-specification/requirements.yaml");
    await user.click(screen.getByRole("link", { name: "the web" }));
    expect(onOpenPath).toHaveBeenCalledTimes(1); // web links are never followed inside the app
  });

  it("drops script-bearing URL schemes from links and images", () => {
    const text = [
      "[a](javascript:alert(1)) [b](JaVaScRiPt:alert(1)) [c](vbscript:msgbox(1))",
      "[d](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
      "![e](javascript:alert(1))",
    ].join("\n\n");
    const { container } = render(<MarkdownView text={text} path="01-brief/brief.md" />);
    for (const node of container.querySelectorAll("[href], [src]")) {
      const url = (node.getAttribute("href") ?? node.getAttribute("src") ?? "").trim().toLowerCase();
      expect(url).not.toMatch(/^(javascript|vbscript|data):/);
    }
    expect(container.innerHTML).not.toMatch(/\son\w+=/i);
  });
});
