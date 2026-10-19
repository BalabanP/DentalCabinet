import JSZip from "jszip";
import { api, fetchFile } from "./api";
import type { Locale } from "./types";

/**
 * Build the patient's export in the browser: the PDF summary from the server plus every attached file,
 * fetched straight from storage, in one ZIP. Doing it here avoids the server's 30-second limit for
 * patients with many large files.
 */
export async function downloadPatientExport(userId: string, lang: Locale): Promise<void> {
  const bundle = await api.exportPatient(userId, lang);
  const zip = new JSZip();
  zip.file(`${bundle.filename}.pdf`, bundle.summary_pdf, { base64: true });

  if (bundle.documents.length) {
    const folder = zip.folder(bundle.documents_folder)!;
    const used = new Set<string>();
    for (const doc of bundle.documents) {
      // Two files with the same name would overwrite each other in the ZIP: number the repeats.
      let name = doc.name;
      for (let n = 2; used.has(name.toLowerCase()); n++) {
        const dot = doc.name.lastIndexOf(".");
        name = dot > 0 ? `${doc.name.slice(0, dot)} (${n})${doc.name.slice(dot)}` : `${doc.name} (${n})`;
      }
      used.add(name.toLowerCase());
      folder.file(name, await fetchFile(doc.url));
    }
  }

  const blob = await zip.generateAsync({ type: "blob" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${bundle.filename}.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
