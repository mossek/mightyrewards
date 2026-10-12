# Mighty Car-Wash Rewards

Loyalty app for the counter: look up customers by cell number, add washes, give a Free Wash on the 10th,
and send the customer a WhatsApp message with their stamp card.

| Folder / file | What it is |
|---|---|
| `app/` | The web app staff use (one page) plus the card images in `app/cards/` |
| `api/` | The server: PIN login, customer records, WhatsApp sending |
| `cards/`, `make_cards.py` | Card image designs. Run `python make_cards.py`, then copy `cards/*.png` to `app/cards/` |
| `tests/` | Server tests: `python -m unittest tests.test_core` |
| `dev_server.py` | Run everything on this PC for a trial (data is not saved) |
| `deploy.ps1` | Puts it on Azure |

## Editing and publishing changes (GitHub)

Every change pushed to the `main` branch is tested and published to Azure automatically
(GitHub → **Actions** tab → "Test and deploy"). It takes about 3–5 minutes. If a test fails, nothing is published.

- **Small text change:** on github.com, open the file (for example `app/index.html`), click the pencil ✏️, edit, then **Commit changes**.
- **Bigger changes:** edit on your PC, then:
  ```
  git add -A
  git commit -m "Describe the change"
  git push
  ```
- **Where things are:** screen layout and buttons → `app/index.html`; WhatsApp message wording → `api/core.py` (`TEMPLATES`, which must match the templates approved in Meta);
  card design → `make_cards.py`, then copy `cards/*.png` into `app/cards/`.

Secrets (PINs, WhatsApp token, storage key) are **not** in GitHub. They live in Azure → Static Web App → Environment variables.
The GitHub secret `AZURE_STATIC_WEB_APPS_API_TOKEN` lets GitHub publish to Azure; if it ever stops working, copy a new one from
Azure → Static Web App → Overview → **Manage deployment token**, and paste it in GitHub → Settings → Secrets and variables → Actions.

## Try it on this PC

```
python dev_server.py
```
Open http://localhost:8080. Staff PIN `1234`, owner PIN `246810` (local trial only).

## Put it on Azure

1. Install the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli-windows) and [Node.js LTS](https://nodejs.org).
2. In PowerShell, in this folder: `az login`, then `.\deploy.ps1`.
3. It asks for an **owner PIN** (6+ digits, for you) and a **staff PIN** (4 digits).
4. When it finishes it prints the web address. Open it on the counter phone, then "Add to Home screen".

Later code changes: `.\deploy.ps1 -UpdateOnly`.

What it creates in resource group `mighty-rewards-rg`:
- **Storage account** in South Africa North (customer records). Cost: cents per month.
- **Static Web App, Free plan** in West Europe (the app and the server). Cost: R0.

## First thing after going live: wash types and staff

Sign in with the **owner PIN** → **Settings** → add each wash type with its price and how many stamps it gives
(for example a valet = 2 stamps), and each staff member's name → **Save**. Staff then pick the wash type and
who washed the car for every wash. Today's screen shows takings, washes per staff member and per wash type.

Customers can be found by **cell number or car registration**. A customer can have up to 5 cars; if they have
more than one, staff pick which car is being washed. **Undo last wash** reverses the most recent wash for that
customer, including its stamps and price.

## PINs

- **Staff PIN (4 digits):** for daily use. Signs in for 12 hours, locks after 15 minutes without use.
- **Owner PIN:** opens the Settings tab, where you change the staff PIN. Changing it signs every staff phone out.
- 5 wrong PINs from one phone locks that phone out for 15 minutes.
- Forgot the owner PIN? Azure portal → Static Web App → Environment variables → `ADMIN_PIN`.

## Turn on automatic WhatsApp messages

Until this is done, staff send each message themselves with the **Open in WhatsApp** button (text only, no card picture).

### 1. Create the message templates

In **WhatsApp Manager → Message templates → Create template**, create these five. Use the names exactly,
language **English** (`en`), and paste the body text exactly. For the image header, upload `cards/card-3.png` as the sample.
If you have a Google review link, add a **Visit website** button named **Rate Our Service** to the first four.

| Name | Category | Header | Body |
|---|---|---|---|
| `loyalty_welcome` | Utility | Image | see below |
| `loyalty_stamp` | Utility | Image | see below |
| `loyalty_full` | Utility | Image | see below |
| `loyalty_redeemed` | Utility | Image | see below |
| `car_ready` | Utility | none | see below |

The exact body text for each template is in `api/core.py` under `TEMPLATES`. Sample values for the variables:
`{{1}}` = Thabo, `{{2}}` = 3, `{{3}}` = 6.

Meta may move a template to the Marketing category, which costs more per message. If that happens, tell me and we'll adjust the wording.

### 2. Connect the number

- **Coexistence (same number as the WhatsApp Business app),** through a partner such as 360dialog. Their API key goes in `WA_TOKEN` and `WA_PROVIDER=360dialog`.
- **Direct to Meta with a new number:** create a permanent System User token in Meta Business Settings.
  Put it in `WA_TOKEN`, the number's Phone number ID in `WA_PHONE_NUMBER_ID`, and set `WA_PROVIDER=meta`.

### 3. Switch it on

```
az staticwebapp appsettings set -n mighty-rewards -g mighty-rewards-rg --setting-names "WA_PROVIDER=meta" "WA_TOKEN=..." "WA_PHONE_NUMBER_ID=..."
```
The Settings tab then shows "Automatic sending is on". If a message fails, staff see the reason and the **Open in WhatsApp** button as a backup.

Optional: `REVIEW_URL=https://g.page/r/...` adds "Rate our service" with the link to the manual (button) messages.
