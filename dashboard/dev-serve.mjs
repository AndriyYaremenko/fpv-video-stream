// dashboard/dev-serve.mjs — DEV ONLY. Serves dashboard/public/ with no auth so the fixture
// preview (index.html?preview=1) and the styleguide (dev-preview.html) render with no backend.
import express from 'express';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();
app.use(express.static(join(__dirname, 'public')));
const port = Number(process.env.DEV_PORT || 8081);
app.listen(port, '127.0.0.1', () =>
  console.log(`Dev preview: http://127.0.0.1:${port}/dev-preview.html  and  http://127.0.0.1:${port}/index.html?preview=1`));
