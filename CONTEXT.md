# QueryPilot Commerce Analytics

This context describes the commerce facts exposed to natural-language analytics. It keeps source-system identities intact so graph traversals do not silently change business meaning.

## Language

**Customer Record**:
An Olist delivery/customer row identified by `customer_id`; it is associated with one order and is not a stable person identity.
_Avoid_: Buyer, user account

**Customer Identity**:
The cross-order buyer identity identified by `customer_unique_id`; multiple Customer Records may refer to the same identity.
_Avoid_: Customer Record

**Order**:
A checkout transaction identified by `order_id`, with lifecycle and delivery timestamps.
_Avoid_: Purchase, payment

**Order Item**:
A product line within an Order, identified by the pair `order_id` and `order_item_id`; seller, price, freight, and shipping deadline belong to this line.
_Avoid_: Product, Order

**Payment**:
One payment attempt or method row for an Order, identified by `order_id` and `payment_sequential`; one Order may have multiple Payments.
_Avoid_: Order value, revenue

**Review**:
Customer feedback associated with an Order; source review IDs are retained even when repeated source rows require separate document identities.
_Avoid_: Product rating

**Product Category**:
The source Portuguese category and its available English translation assigned to a Product.
_Avoid_: Product type

## Customer Support Language

**Company**:
An organization holding a support contract and able to raise multiple Cases.
_Avoid_: Customer Record, individual user

**Case**:
One customer support request with a lifecycle state, urgency, response time, resolution time,
and optional post-resolution customer score.
_Avoid_: Order, Review

**Specialist**:
The primary support professional assigned to a Case.
_Avoid_: Seller, account owner

**Topic**:
The operational issue classification assigned to a Case.
_Avoid_: Product Category

**Case Event**:
One timestamped activity within a Case. Event work minutes are activity effort, not total Case
resolution time.
_Avoid_: Case, status history snapshot
