# Connect WhatsApp (automatic card messages)

Customers get their loyalty card picture on WhatsApp automatically, from a **new number** that only sends
loyalty messages. 078 667 7610 stays on the WhatsApp Business app for normal chats.

You do the Meta steps (they need your Facebook login). Secrets go into **Azure**, never into chat or GitHub.

## Step 1 — New SIM
Any prepaid SIM. Put it in a phone that can receive SMS. **Do not install WhatsApp on it.**

## Step 2 — Meta app
1. developers.facebook.com → **My Apps → Create app** → use case **Other** → type **Business**.
2. Name `Mighty Rewards`, pick your Meta Business account (Mighty Car-Wash).
3. On the app dashboard: **WhatsApp → Set up**.

## Step 3 — Add the number
1. **WhatsApp → API Setup → Add phone number**.
2. Display name **Mighty Car-Wash**, category **Automotive**, then verify the new SIM with the SMS code.
3. Copy the **Phone number ID** (long number under the phone number). It is not secret.

## Step 4 — Payment method
WhatsApp Manager (business.facebook.com/wa/manage) → **Settings → Payment methods** → add a card.
Meta charges per automatic message (cents each for these utility messages).

## Step 5 — Permanent access token
1. business.facebook.com → **Business settings → Users → System users → Add** (role **Admin**).
2. **Assign assets**: your app (full control) and your WhatsApp account (full control).
3. **Generate new token** → app `Mighty Rewards` → expiry **Never** → tick
   `whatsapp_business_messaging` and `whatsapp_business_management` → copy the token.

## Step 6 — App secret
developers.facebook.com → your app → **App settings → Basic → App secret → Show** → copy it.

## Step 7 — Put the secrets in Azure (you do this)
portal.azure.com → Static Web App **mighty-rewards** → **Environment variables** → **+ Add**:

| Name | Value |
|---|---|
| `WA_TOKEN` | token from Step 5 |
| `WA_APP_SECRET` | app secret from Step 6 |
| `WA_PHONE_NUMBER_ID` | Phone number ID from Step 3 |
| `WA_PROVIDER` | change from `off` to `meta` |

Click **Apply**. (Leave `WA_PROVIDER=off` until the templates in Step 9 are approved, if you prefer.)

## Step 8 — Webhook (so customer replies reach the app)
developers.facebook.com → your app → **WhatsApp → Configuration → Webhook → Edit**:
- **Callback URL:** `https://happy-flower-011ac9603.1.azurestaticapps.net/api/whatsapp`
- **Verify token:** the value of `WA_VERIFY_TOKEN` in Azure → Environment variables
- Click **Verify and save**, then under **Webhook fields** subscribe to **messages**.

## Step 9 — Message templates
WhatsApp Manager → **Message templates → Create template**. Create all five with these exact names and wording.
Approval usually takes minutes to a few hours. If Meta moves one to **Marketing**, tell whoever maintains the app;
the wording may need a small change.

### 1. `loyalty_welcome`  (New customer signs up)

- **Category:** Utility  ·  **Language:** English
- **Header:** **Image** (upload `app/cards/card-3.png` as the sample)
- **Body** (copy exactly):

```
Hi {{1}} 🚗✨

Welcome to Mighty Car-Wash Rewards! Your first wash is on your loyalty card. Every 10th wash is a Free Wash.

Just give your cell number at the counter each visit. Reply STOP if you don't want these messages.
```

- **Sample values:** `{{1}}` = Thabo
- **Button** (optional, add when you have the link): *Visit website* → text `Rate Our Service` → your Google review link

### 2. `loyalty_stamp`  (Each paid wash)

- **Category:** Utility  ·  **Language:** English
- **Header:** **Image** (upload `app/cards/card-3.png` as the sample)
- **Body** (copy exactly):

```
Hi {{1}} 🚗✨

Your Mighty Car-Wash loyalty card has been updated! You've done {{2}} of 9 washes.

Only {{3}} more to go until your Free Wash. 💦
```

- **Sample values:** `{{1}}` = Thabo, `{{2}}` = 3, `{{3}}` = 6
- **Button** (optional, add when you have the link): *Visit website* → text `Rate Our Service` → your Google review link

### 3. `loyalty_full`  (Card is full (next wash free))

- **Category:** Utility  ·  **Language:** English
- **Header:** **Image** (upload `app/cards/card-3.png` as the sample)
- **Body** (copy exactly):

```
Hi {{1}} 🎉

Your Mighty Car-Wash loyalty card is full! Your next wash is a Free Wash.

Just give your cell number at the counter.
```

- **Sample values:** `{{1}}` = Thabo
- **Button** (optional, add when you have the link): *Visit website* → text `Rate Our Service` → your Google review link

### 4. `loyalty_redeemed`  (Free Wash given)

- **Category:** Utility  ·  **Language:** English
- **Header:** **Image** (upload `app/cards/card-3.png` as the sample)
- **Body** (copy exactly):

```
Enjoy your Free Wash, {{1}}! 🎉

Your new Mighty Car-Wash loyalty card has started. Keep that shine coming!
```

- **Sample values:** `{{1}}` = Thabo
- **Button** (optional, add when you have the link): *Visit website* → text `Rate Our Service` → your Google review link

### 5. `car_ready`  (Staff tap “Car is ready for collection”)

- **Category:** Utility  ·  **Language:** English
- **Header:** None
- **Body** (copy exactly):

```
Hi {{1}}, your vehicle is sparkling and ready for collection at Mighty Car-Wash. 🚗✨
```

- **Sample values:** `{{1}}` = Thabo
- **Button** (optional, add when you have the link): *Visit website* → text `Rate Our Service` → your Google review link

## Step 10 — Test
1. In the app, look up **your own cell number** and record a wash.
2. Within seconds you should get the WhatsApp with the card picture. The app shows **✓ Sent on WhatsApp**.
3. If it shows an error instead, the message explains why (e.g. template not approved yet) and the
   **Open in WhatsApp** button still works as a backup.
4. Reply "hello" to the message: you get the automatic answer, and it appears in the **Replies** tab.

## Limits for new accounts
New WhatsApp accounts can message about **250 different customers per 24 hours**. That rises automatically
as you send good-quality messages, or straight away to 1,000+ once Meta **business verification** is done
(Business settings → Security Centre → Start verification).
