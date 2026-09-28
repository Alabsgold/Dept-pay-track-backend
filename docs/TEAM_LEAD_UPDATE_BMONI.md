# Update on Payments + BMONI - From Emmanuel (Backend)

Hello,

This is my update on where the system is now and what I have done on payments and BMONI.

## 1. What our system does today

Our system is like a dues office inside the phone. It replaces collecting money by hand and asking for screenshots.

We have 3 kinds of users:
- Student: sees only fees for his own department, pays, sees his own history and receipts.
- Class rep: same as student, plus creates fees, edits fees, closes fees, sees who has paid and who is owing, marks cash payment by hand, helps student reset password.
- Admin: sees everything, uploads full class list, makes a student a rep, reviews problem payments.

A student only sees his own department. A rep only controls his own department.

How person joins:
- If you sign up yourself: you give username, email, password, school ID number, department, level. Then you login with password plus username or email or school ID.
- If school already added you: admin can upload the full class list from a name list file. So even if you never sign up, we still know you and you cannot dodge fees.
- If you were added that way, you claim your account: you give school ID + first name + the code admin shared, then you choose your password and email. Now it is yours.
- If you forget password: rep or admin gives you a one-time code in person and you use it to set a new password. No email needed.

How fee works:
Rep creates fee like "Departmental Dues - 3500". Each fee has title, amount, closing date, whether compulsory, which level it is for. Closing a fee does not delete it, it only stops new payment. Record stays because money already moved.

## 2. The two ways to pay - they are separate

**Road 1 - Pay online with card/bank (Paystack):**
Student presses Pay Now. System makes a tracking number and asks Paystack for a checkout page. Student goes there and pays. Paystack tells our system directly: this tracking number was paid, here is the amount. We check: is amount exactly correct? Is fee still open? Has student already paid? If yes, we mark paid, show receipt, send message. If amount is wrong, we put it in review list for admin to refund by hand. Student can also press refresh status.

This road is fully working today.

**Road 2 - Pay by direct transfer to department account (BMONI):**
Each department can have its own real bank account number. Rep fills form: first name, last name, email, phone, and 11-digit ID number of the trusted person behind the account. BMONI checks the ID and gives a 10-digit account number with bank name, e.g. PROVIDUS BANK 9845221370. We save it and show it on Pay by Transfer card. Student copies it, opens his own bank app, and sends exact amount.

That account display part is built. When money lands later, BMONI will send us automatic message. Today we only file and keep that message safely. Marking student paid from it automatically is stage 2.

Paystack and BMONI never talk to each other. Paystack road uses Paystack only. Transfer road uses BMONI only.

Other things working: My payments list, receipt page, fee roster for rep (who paid, how, when, who is owing), problem list for admin, automatic messages when new fee comes or payment succeeds/fails, and reports of total expected, total collected, who never paid.

## 3. What I have already built for BMONI

All code is done and tested:
- Settings place for BMONI key and address is ready.
- Code knows how to send person details to BMONI, check ID, start Nigeria account, read account back.
- Code saves it per department: account name, number, bank name. One department has one account. One person cannot stand for two departments.
- We never save full ID number, only last 4 digits.
- Pay by Transfer card shows the saved account. If no key or no account, it honestly says not available, it never shows fake number.
- Messages from BMONI are filed safely as proof.
- 43 tests pass for this part alone.

What is not done is our own real key from BMONI. We only tested with their public shared test key from docs. That key gave us one real account (9845221370 / PROVIDUS BANK / Dillon Bunch) then stopped giving new ones. New people now get empty. That is limit on their side, not our code.

## 4. Bottleneck and my decision

Two problems:

A. Shared test key is limited. I cannot reliably create fresh accounts live on stage. If I click create with new name on stage, it may answer empty. Our code will honestly say try later instead of faking a number. That is good behaviour but risky on stage.

B. Matching problem: if 20 students send to same department number at once, BMONI only tells us amount + sender name + time. It does not know which school ID or which fee. Wrong amounts cannot auto-count, they must go to review. Same names can confuse. Failed transfers send nothing.

My decision to avoid failure on demo day:
Keep both options, but make Paystack card the default we demo live, and show BMONI transfer card with the saved real account as second option. That way we meet the hackathon rule of build BMONI into system, and we still have working flow.

For matching, I suggest Stage 2: give each student a short code like TRF-ABC123 to write in bank note when sending, then we match by that code. Alternative is Paystack personal numbers per student, but that is more work and weakens department pot story. For now, transfer proof is filed and rep confirms by hand with mark-as-paid button. No wrong crediting.

## 5. What I need from you

1. Approve demo plan: Paystack live, BMONI shown with saved account.
2. Tell me if you prefer code-in-note (my choice) or personal numbers for after hackathon.
3. Help get our own BMONI key after hackathon if we go live. Code needs only 2 values filled, no rebuild.

Nothing changes for users. Paystack stays. BMONI is background account per department.

Thanks,
Emmanuel
