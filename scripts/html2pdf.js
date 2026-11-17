// Usage: node scripts/html2pdf.js input.html output.pdf  (A4, print CSS, backgrounds on)
const { chromium } = require('playwright');
(async () => {
  const [inp, out] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.emulateMedia({ media: 'print', colorScheme: 'light' });
  await p.goto('file://' + require('path').resolve(inp), { waitUntil: 'load' });
  await p.waitForTimeout(300);
  await p.pdf({ path: out, format: 'A4', printBackground: true, margin: { top: '14mm', bottom: '16mm', left: '14mm', right: '14mm' },
                displayHeaderFooter: true, headerTemplate: '<div></div>',
                footerTemplate: '<div style="font-size:8px;width:100%;padding:0 14mm;color:#555;display:flex;justify-content:space-between"><span>Milieu Consult Limited</span><span class="pageNumber"></span></div>' });
  await b.close();
})();
