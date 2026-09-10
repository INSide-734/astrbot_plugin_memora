export function normalizeHtmlLineEndings(html: string): string {
  return html.replace(/\r\n?/g, "\n");
}
