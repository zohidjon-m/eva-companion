# extract_entry.md test report

model: gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf
temp: 0.3
json_mode: False
parseable: 10/10 | schema-valid: 10/10

---

### Entry 1 — OK (41.4s)

ENTRY:
Can't sleep. The interview at Meridian is at 9am and my brain won't shut up. I went through the system design questions twice and re-read my notes on their product but I still feel like I'm going to freeze the second they ask me something I don't know. I want to come across as calm and curious, not desperate. Whatever happens I just need to get through tomorrow.

RAW OUTPUT:
{
"mood": -3,
"emotions": [
{"name": "anxiety", "intensity": 0.8},
{"name": "frustration", "intensity": 0.6}
],
"entities": [
{"name": "Meridian", "type": "place", "normalized": "meridian"}
],
"themes": [
"interview stress",
"sleep",
"performance anxiety"
],
"events": [
"could not sleep",
"went through system design questions twice",
"re-read notes on the product"],
"stated_goals": [
{"text": "come across as calm and curious, not desperate", "is_new": true},
{"text": "get through tomorrow", "is_new": true}
],
"behaviors": [
"went through system design questions twice",
"re-read notes on the product"],
"decisions": [],
"open_loops": [
{"description": "fear of freezing during unknown questions", "status": "open"}],
"self_judgments": [
"feels like they will freeze",
"wants to avoid appearing desperate"],
"summary": "They are experiencing significant anxiety and insomnia due to an upcoming interview at Meridian at 9 am. They spent time studying the system design questions twice and re-read product notes, but they still feel a fear of freezing when faced with unknown questions. They express a desire to appear calm and curious rather than desperate. Their primary goal is simply to get through the day tomorrow."}
}


---

### Entry 2 — OK (28.9s)

ENTRY:
I am so done with Kevin leaving his crusty pans in the sink. Three days now. I'm not his mother. I shouldn't have to leave a passive-aggressive note but here we are. Honestly it's such a small thing and yet it makes me irrationally angry every single time I walk into that kitchen.

RAW OUTPUT:
{"mood": -3, "emotions": [{"name": "anger", "intensity": 0.8}, {"name": "frustration", "intensity": 0.7}], "entities": [{"name": "Kevin", "type": "person", "normalized": "kevin"}, {"name": "kitchen", "type": "place", "normalized": "kitchen"}], "themes": ["frustration", "anger", "household", "passive-aggression"], "events": ["Kevin left his pans in the sink for three days", "the speaker walked into the kitchen"], "stated_goals": [{"text": "stop Kevin from leaving his pans in the sink", "is_new": true}], "behaviors": ["felt irrational anger when entering the kitchen", "considered leaving a passive-aggressive note"], "decisions": [], "open_loops": [{"description": "dealing with Kevin's behavior regarding the sink", "status": "open"}], "self_judgments": ["feels irrationally angry", "feels like they shouldn't have to leave a passive-aggressive note"], "summary": "The speaker expressed intense anger and frustration regarding Kevin leaving his dirty pans in the sink for three days. They felt that this small issue made them irrationally angry every time they entered the kitchen. They considered leaving a passive-aggressive note but ultimately did not do so. The entry highlights a strong feeling of exasperation with the ongoing household issue."}


---

### Entry 3 — OK (20.6s)

ENTRY:
Such a good day. Sam and I drove up to Eagle Ridge and did the full loop, maybe eight miles, and the light coming through the pines near the top was unreal. We barely talked, just walked, and it was exactly what I needed. We decided we're going to do one real hike every month this year. I feel lucky to have someone who recharges me instead of draining me.

RAW OUTPUT:
{"mood": 4, "emotions": [{"name": "joy", "intensity": 0.9}, {"name": "contentment", "intensity": 0.8}, {"name": "hope", "intensity": 0.5}], "entities": [{"name": "Sam", "type": "person", "normalized": "sam"}, {"name": "Eagle Ridge", "type": "place", "normalized": "eagle ridge"}], "themes": ["nature", "friendship", "recharge"], "events": ["drove to Eagle Ridge", "completed an eight-mile loop hike", "walked and talked with Sam"], "stated_goals": [{"text": "do one real hike every month this year", "is_new": true}], "behaviors": ["drove to Eagle Ridge", "completed a full loop hike", "walked with Sam"], "decisions": ["decided to do one real hike every month this year"], "open_loops": [], "self_judgments": ["feels lucky to have someone who recharges them"], "summary": "They had a very positive day while hiking with Sam to Eagle Ridge, describing the experience as exactly what they needed. They completed an eight-mile loop hike, enjoying the view of the pines. They expressed gratitude for Sam, feeling lucky to have someone who recharges them instead of draining them. They resolved to do one real hike every month throughout the year."}


---

### Entry 4 — OK (7.0s)

ENTRY:
Tired. Didn't really do much today. Watched some TV. Meh.

RAW OUTPUT:
{"mood": 0, "emotions": [], "entities": [], "themes": ["tiredness"], "events": ["watched some TV"], "stated_goals": [], "behaviors": ["watched TV"], "decisions": [], "open_loops": [], "self_judgments": [], "summary": "The entry describes a day characterized by low energy and inactivity. The individual spent the day watching television without engaging in any significant activities. There is no strong emotional signal present in the text."}


---

### Entry 5 — OK (23.2s)

ENTRY:
It's three years today since Grandma Rose passed. I didn't call Mom and I feel bad about that — I think I was avoiding it because I knew we'd both cry. I made her lemon cake from the recipe card she wrote out for me, and the kitchen smelled exactly like her house. I miss her more than I expected to this year, not less.

RAW OUTPUT:
{"mood": -3, "emotions": [{"name": "sadness", "intensity": 0.8}, {"name": "guilt", "intensity": 0.6}, {"name": "longing", "intensity": 0.7}], "entities": [{"name": "Grandma Rose", "type": "person", "normalized": "grandma rose"}, {"name": "Mom", "type": "person", "normalized": "mom"}], "themes": ["grief", "family", "memory"], "events": ["It was three years since Grandma Rose passed", "Did not call Mom", "Made lemon cake from Grandma Rose's recipe"], "stated_goals": [{"text": "miss Grandma Rose more than expected this year", "is_new": false}], "behaviors": ["made lemon cake", "avoided calling Mom"], "decisions": [], "open_loops": [{"description": "feeling bad about not calling Mom", "status": "open"}], "self_judgments": ["feels bad about avoiding a call"], "summary": "They are reflecting on the three-year anniversary of Grandma Rose's passing. They feel guilty for not calling their mother, believing they were avoiding the call because they anticipated both would cry. They made lemon cake using a recipe from Grandma Rose, which evoked strong sensory memories of her home. They expressed a deep sense of longing for Grandma Rose that exceeded their expectations for the year."}


---

### Entry 6 — OK (23.0s)

ENTRY:
I think I've figured something out. I keep saying I'm a writer but I haven't written anything real in months. Starting tomorrow I'm going to write for thirty minutes every morning before I touch my phone, no exceptions. Not to publish, just to become the kind of person who actually does the thing instead of just identifying with it.

RAW OUTPUT:
{"mood": 1, "emotions": [{"name": "hope", "intensity": 0.8}, {"name": "contentment", "intensity": 0.5}], "entities": [], "themes": ["writing", "self-improvement", "discipline"], "events": [], "stated_goals": [{"text": "write for thirty minutes every morning before touching phone", "is_new": true}, {"text": "become the kind of person who actually does the thing instead of just identifying with it", "is_new": true}], "behaviors": [], "decisions": ["start writing for thirty minutes every morning"], "open_loops": [], "self_judgments": [], "summary": "The user feels they have found a breakthrough regarding their identity as a writer, realizing they have not produced any real work in months. They have set a new, strict goal to write for thirty minutes every morning before checking their phone. This discipline is intended to help them become a person who acts on their identity rather than just identifying with it. This resolution is framed as a fresh commitment to action."}


---

### Entry 7 — OK (40.0s)

ENTRY:
Priya and I had that argument again about money and it ended the same way — her going quiet and me pretending it's fine. It isn't fine. I hate that we keep circling the same drain and neither of us says the real thing. I love her and I still went to bed angry. I don't know how to bring it up tomorrow without it blowing up again.

RAW OUTPUT:
{"mood": -3, "emotions": [{"name": "sadness", "intensity": 0.8}, {"name": "frustration", "intensity": 0.7}], "entities": [{"name": "Priya", "type": "person", "normalized": "priya"}], "themes": ["relationship conflict", "communication", "money"], "events": ["had an argument with Priya about money", "pretended everything was fine", "went to bed angry"], "stated_goals": [{"text": "find a way to bring up the issue tomorrow without it blowing up again", "is_new": true}], "behaviors": ["had an argument with Priya", "pretended things were fine", "went to bed angry"], "decisions": ["will bring up the issue tomorrow"], "open_loops": [{"description": "doesn't know how to bring up the issue without blowing up", "status": "open"}], "self_judgments": ["hates the cycle of circling the same drain"], "summary": "The user had another argument with Priya regarding money, which resulted in a cycle of pretending things were fine despite the underlying tension. They expressed frustration at the recurring pattern of avoiding honest communication. Despite the feelings, they went to bed angry. They are unsure how to initiate a difficult conversation the following day without causing another conflict. They recognize the negative cycle they are stuck in."}


---

### Entry 8 — OK (41.3s)

ENTRY:
Finished my first 10k this morning, actually ran the whole thing without walking, and I'm genuinely proud of that. Then I undid it by inhaling half a pizza and a tub of ice cream tonight because I 'earned it.' The Aurora launch at work is finally on track though — we shipped the beta and the early numbers look strong. Weird day. Up and down.

RAW OUTPUT:
{"mood": 3, "emotions": [{"name": "pride", "intensity": 0.8}, {"name": "contentment", "intensity": 0.5}], "entities": [{"name": "Aurora launch", "type": "project", "normalized": "aurora launch"}], "themes": ["achievement", "work progress", "self-reward"], "events": ["finished a 10k run without walking", "undid the run by eating pizza and ice cream", "shipped the beta for the Aurora launch", "early numbers for the Aurora launch look strong"], "stated_goals": [{"text": "achieve a successful Aurora launch", "is_new": false}], "behaviors": ["ran a 10k without walking", "ate half a pizza and a tub of ice cream"], "decisions": [], "open_loops": [], "self_judgments": [], "summary": "They experienced a mixed day characterized by a significant physical achievement followed by a self-indulgent indulgence. They successfully completed their first 10k run without walking, which brought a strong sense of pride. This physical success was followed by eating pizza and ice cream as a reward. On a professional front, the Aurora launch is progressing well, with the beta shipped and early metrics showing positive results. The overall day was described as weird but ultimately positive."}


---

### Entry 9 — OK (37.4s)

ENTRY:
Errands. Dropped the car at Pep Boys for the brakes, dentist at 2 (no cavities), picked up groceries at Safeway and finally returned that package. Need to email the landlord about the lease renewal before Friday.

RAW OUTPUT:
{
"mood": 0,
"emotions": [],
"entities": [{"name": "Pep Boys", "type": "place", "normalized": "pep boys"}, {"name": "Safeway", "type": "place", "normalized": "safeway"}],
"themes": ["errands", "logistics", "admin"],
"events": ["dropped the car at Pep Boys for brakes", "visited the dentist", "picked up groceries at Safeway", "returned a package"],
"stated_goals": [{"text": "email the landlord about the lease renewal before Friday", "is_new": true}],
"behaviors": ["dropped the car at Pep Boys", "visited the dentist", "picked up groceries at Safeway", "returned a package"],
"decisions": ["email the landlord about the lease renewal before Friday"],
"open_loops": [],
"self_judgments": [],
"summary": "The entry details a series of errands completed, including dropping off a car for brake repairs at Pep Boys, visiting the dentist, and picking up groceries at Safeway. The individual also returned a package. A remaining task is to email the landlord regarding the lease renewal before the upcoming Friday."
}


---

### Entry 10 — OK (40.7s)

ENTRY:
Heavy night, lots on my mind. The money thing is back — rent went up again and I ran the numbers and we're basically breaking even every month with nothing left over, which terrifies me when I think about Dad's hospital bills coming. He sounded weaker on the phone today and Mom won't admit how bad it's getting. I prayed for the first time in a while, which surprised me; I don't even know if I believe but it felt like something to do with the fear. I did go for a run to clear my head and that helped a little. I want to be the person my family can lean on, the steady one, but right now I just feel like I'm holding water in my hands. I keep telling myself to make a real budget and I keep not doing it.

RAW OUTPUT:
{"mood": -4, "emotions": [{"name": "fear", "intensity": 0.9}, {"name": "anxiety", "intensity": 0.8}, {"name": "sadness", "intensity": 0.6}], "entities": [{"name": "Dad", "type": "person", "normalized": "dad"}, {"name": "Mom", "type": "person", "normalized": "mom"}], "themes": ["financial stress", "family worry", "health", "anxiety"], "events": ["ran the numbers on finances", "heard Dad sounded weaker on the phone", "prayed for the first time in a while", "went for a run"], "stated_goals": [{"text": "be the person my family can lean on, the steady one", "is_new": false}, {"text": "make a real budget", "is_new": true}], "behaviors": ["ran the numbers on finances", "went for a run"], "decisions": [], "open_loops": [{"description": "wants to make a real budget but keeps failing to do so", "status": "open"}], "self_judgments": ["feels like they are holding water in their hands"], "summary": "They experienced a heavy night dominated by financial stress after realizing their household is breaking even with no surplus. This situation is compounded by worrying about their father's worsening health and their mother's reluctance to admit the severity. They felt a surprising sense of fear while praying for the first time. They managed to run for a run to clear their head, which provided some temporary relief. They expressed a desire to be a steady support for their family and a self-criticism about failing to create a budget."}


---
