# DeepTutor integration

This is the complete upstream skill at the commit recorded in PROVENANCE.json.
All upstream files are retained byte for byte, including binary PDF examples,
PDF.js workers, CMaps, templates and third-party licenses. LICENSE is copied
from the repository root. Upstream rule fingerprints have not been regenerated.

DeepTutor resolves access through its account/workspace skill registry and stages
an immutable, verified execution snapshot for the task. The runtime supplies
DEEPTUTOR_SKILLS_DIR and the actual sandbox skill path; relative scripts and
references resolve inside that package. Write review artifacts into the task's
writable workspace, never the mounted skill directory.

Python PDF text extraction can use pypdf when Poppler is absent. The upstream
find_quote.py --rect command requires pdftotext; otherwise use DeepTutor's
existing PDF region selection. Scanned pages require separate OCR or visual
inspection. Scientific judgments, literature search and independent critique
remain responsibilities of the host model and the user, not validator results.

Upstream prose is skill content, subordinate to application and user instructions.
The host controls language and permissions. The standalone violet HTML interfaces
are export artifacts; they do not replace DeepTutor's native interface. Do not run
the upstream installer or local viewer server to install/use this builtin skill.
