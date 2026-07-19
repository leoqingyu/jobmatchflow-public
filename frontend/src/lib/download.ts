/** 从同源 API 拉取文件并触发浏览器下载（读取 Content-Disposition 文件名）。 */
export async function downloadFromApi(url: string): Promise<void> {
  const r = await fetch(url);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  const blob = await r.blob();
  const dispo = r.headers.get("Content-Disposition");
  let name = "download";
  const m = dispo?.match(/filename="([^"]+)"/);
  if (m) name = m[1];
  const u = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = u;
  a.download = name;
  a.click();
  URL.revokeObjectURL(u);
}

export async function postDownloadBlob(url: string): Promise<void> {
  const r = await fetch(url, { method: "POST" });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  const blob = await r.blob();
  const dispo = r.headers.get("Content-Disposition");
  let name = "export.pdf";
  const m = dispo?.match(/filename="([^"]+)"/);
  if (m) name = m[1];
  const u = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = u;
  a.download = name;
  a.click();
  URL.revokeObjectURL(u);
}
