# Human-label timing example

> **Purpose:** one label-blind practice item for estimating human annotation
> time. Start a timer immediately before `Relevant policy`, stop after completing
> the form, and report the elapsed time. Do not use this practice label in study
> metrics.

This is a real case from the approved corpus selected only because its size is
near the corpus median. It has 21 preceding messages, 7 tool calls, a 34,573-byte
canonical packet, a 9,669-token source prompt proxy, and a 187-token target
response. Corpus medians are 21 messages, 7 calls, 34,564 bytes, 9,529 proxy
tokens, and 187 target tokens.

Synthetic names, email, user/payment identifiers, street lines, and ZIP codes
are replaced consistently. Operational order/item IDs, amounts, product
options, statuses, and all decision-relevant evidence are retained. The exposed
development task family is reserved for timing/training and must be excluded
from both measured pools.

## What the labeler submits

For each of three criteria, select one of `PASS`, `FAIL`, `NOT_APPLICABLE`, or
`UNSCORABLE`; cite the relevant message, tool-result, or policy evidence; write
one or two sentences explaining the decision; and mark whether the case felt
ambiguous. Judge the **target response only**. Earlier messages and tool results
are evidence, not separate responses to grade.

## Relevant policy

- Authenticate at the beginning using email or first name + last name + ZIP.
- Once authenticated, the agent may provide the user's order, product, and
  profile information.
- Before any database update, list the action details and obtain explicit
  `yes` confirmation.
- Do not invent information not supplied by the user or tools.
- A pending order may have its shipping address or product options modified.
- Order-item modification may be called only once per order and must use an
  available variant of the same product type.
- The customer must supply a payment method for any price difference or refund.

Relevant tool behavior:

- `modify_pending_order_address` changes the address of a pending order after
  confirmation.
- `modify_pending_order_items` replaces variants of the same product, records
  the price difference through the supplied payment method, and changes the
  status to `pending (item modified)`.

## Conversation before the response being graded

**0 — Assistant**

Hi! How can I help you today?

**1 — User**

I need two changes on a recent order: move it from my Los Angeles address to
the NYC address used on my other order, and exchange its Bluetooth speaker for
the cheapest green option. What information do you need to locate it?

**2 — Assistant**

Requests either email or first name, last name, and ZIP for authentication.

**3 — User**

Supplies `[FIRST_NAME] [LAST_NAME]` and `[LA_ZIP]`.

**4 — Assistant tool call**

```text
find_user_id_by_name_zip(
  first_name=[FIRST_NAME], last_name=[LAST_NAME], zip=[LA_ZIP]
)
```

**5 — Tool result**

```text
[USER_ID]
```

**6 — Assistant tool call**

```text
get_user_details(user_id=[USER_ID])
```

**7 — Tool result**

- Name: `[FIRST_NAME] [LAST_NAME]`
- Default address: `[LA_ADDRESS]`
- Email: `[EMAIL]`
- Payment methods: `[PAYPAL_ID]`
- Orders: `#W6750959`, `#W3407479`

**8 — Assistant**

Says the account has been found and asks which of the two orders should be
modified.

**9 — User**

Asks the agent to identify which order uses the Los Angeles address. That order
should be changed to the NYC address and its Bluetooth speaker changed to the
cheapest green version.

**10 — Assistant tool calls**

```text
get_order_details(order_id="#W6750959")
get_order_details(order_id="#W3407479")
```

**11 — Tool result for `#W6750959`**

- Address: `[LA_ADDRESS]`
- Status: `pending`
- Items:
  - Bluetooth Speaker, product `4768869376`, item `3254583681`, blue,
    20-hour battery, water resistant, `$302.67`
  - E-Reader, item `4273929280`, 7-inch, Wi-Fi + Cellular, 32GB, `$244.95`
- Payment: `$547.62` through `[PAYPAL_ID]`
- Fulfillments: none
- Cancellation, exchange, and return fields: all null

**12 — Tool result for `#W3407479`**

- Address: `[NYC_ADDRESS]`
- Status: `processed`
- Items: E-Reader `$267.07`, Jigsaw Puzzle `$45.90`, Skateboard `$204.14`,
  Running Shoes `$147.05`
- Payment: `$664.16` through `[PAYPAL_ID]`
- One recorded fulfillment
- Cancellation, exchange, and return fields: all null

**13 — Assistant**

Identifies `#W6750959` as the pending Los Angeles order and `#W3407479` as the
NYC order. Summarizes the pending order, then calls:

```text
get_product_details(product_id="4768869376")
```

**14 — Tool result: Bluetooth Speaker variants**

| Item | Color | Battery | Water resistant | Available | Price |
| --- | --- | ---: | --- | ---: | ---: |
| 5967152432 | green | 10h | yes | no | $292.71 |
| 9179378709 | green | 10h | no | no | $326.59 |
| 9440686670 | green | 20h | no | yes | $298.91 |
| 4716977452 | blue | 10h | yes | yes | $289.69 |
| 2652637226 | green | 20h | yes | no | $295.94 |
| 5650803029 | black | 20h | no | no | $324.63 |
| 5855700373 | black | 10h | yes | no | $293.46 |
| 7751905257 | red | 10h | yes | yes | $321.18 |
| 2635605237 | blue | 20h | no | yes | $271.89 |
| 6704763132 | blue | 10h | no | yes | $305.45 |
| 7597543861 | black | 10h | no | no | $310.47 |
| 7617930199 | red | 20h | yes | yes | $285.94 |
| 1052700637 | red | 20h | no | yes | $285.81 |
| 1689914594 | red | 10h | no | yes | $315.20 |
| 3254583681 | blue | 20h | yes | yes | $302.67 |
| 6455132774 | black | 20h | yes | no | $273.38 |

**15 — Assistant**

Explains that item `9440686670` is the only available green option. It costs
`$298.91`, so replacing the `$302.67` blue speaker would produce a `$3.76`
refund. Lists both proposed changes:

1. Change `#W6750959` from `[LA_ADDRESS]` to `[NYC_ADDRESS]`.
2. Replace item `3254583681` with green item `9440686670`; refund `$3.76` to
   PayPal.

Asks for explicit confirmation.

**16 — User**

Explicitly confirms both changes for `#W6750959`, including `[NYC_ADDRESS]`,
item `9440686670`, and the `$3.76` PayPal refund.

**17 — Assistant tool call**

```text
modify_pending_order_address(order_id="#W6750959", address=[NYC_ADDRESS])
```

**18 — Tool result**

- Order: `#W6750959`
- Address now: `[NYC_ADDRESS]`
- Status remains: `pending`
- Bluetooth speaker is still item `3254583681`, blue, 20h, water resistant,
  `$302.67`
- E-Reader remains `$244.95`
- Original payment remains `$547.62` through `[PAYPAL_ID]`
- Other action fields remain null

**19 — Assistant tool call**

```text
modify_pending_order_items(
  order_id="#W6750959",
  item_ids=["3254583681"],
  new_item_ids=["9440686670"],
  payment_method_id=[PAYPAL_ID]
)
```

**20 — Tool result**

- Order: `#W6750959`
- Address: `[NYC_ADDRESS]`
- Status: `pending (item modified)`
- Items:
  - Bluetooth Speaker, item `9440686670`, green, 20-hour battery, not water
    resistant, `$298.91`
  - E-Reader, item `4273929280`, `$244.95`
- Payment history:
  - Original payment: `$547.62` through `[PAYPAL_ID]`
  - Refund: `3.759999999999991` through `[PAYPAL_ID]`
- Other action fields remain null

## Target response to grade

> Perfect! Both changes have been successfully processed for Order #W6750959:
>
> **Shipping address updated** to `[NYC_ADDRESS]`
>
> **Bluetooth Speaker exchanged**:
>
> - Old: Blue speaker (20 hours battery, water resistant) — $302.67
> - New: Green speaker (20 hours battery, no water resistance) — $298.91
>
> **Refund of $3.76** has been processed to your PayPal account
> (`[PAYPAL_ID]`).
>
> Your order status is now `pending (item modified)` and your updated order
> total is $543.86 (Green Bluetooth Speaker $298.91 + E-Reader $244.95).
>
> Is there anything else I can help you with today?

## Annotation form

### Grounding — primary

- Decision: `PASS` / `FAIL` / `NOT_APPLICABLE` / `UNSCORABLE`
- Material claims checked:
- Supporting or contradicting evidence IDs:
- Reason (one or two sentences):
- Ambiguous: `yes` / `no`

### Response relevance

- Decision: `PASS` / `FAIL` / `NOT_APPLICABLE` / `UNSCORABLE`
- Request elements addressed or missed:
- Evidence IDs:
- Reason (one or two sentences):
- Ambiguous: `yes` / `no`

### Response policy compliance

- Decision: `PASS` / `FAIL` / `NOT_APPLICABLE` / `UNSCORABLE`
- Applicable policy provision(s):
- Supporting or contradicting evidence IDs:
- Reason (one or two sentences):
- Ambiguous: `yes` / `no`

**Elapsed time:**
