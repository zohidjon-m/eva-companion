# extract_entry.md test report

model: gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf
temp: 1.0
json_mode: False
parseable: 9/10 | schema-valid: 7/10

---

### Entry 1 — OK (32.1s)

ENTRY:
Can't sleep. The interview at Meridian is at 9am and my brain won't shut up. I went through the system design questions twice and re-read my notes on their product but I still feel like I'm going to freeze the second they ask me something I don't know. I want to come across as calm and curious, not desperate. Whatever happens I just need to get through tomorrow.

RAW OUTPUT:
```json
{"mood": -3, "emotions": [{"name": "anxiety", "intensity": 0.9}, {"name": "fear", "intensity": 0.7}], "entities": [{"name": "Meridian", "type": "place", "normalized": "meridian"}], "themes": ["interview stress", "sleep", "performance anxiety"], "events": ["could not sleep", "went through system design questions twice", "re-read notes on the product"], "stated_goals": [{"text": "come across as calm and curious, not desperate", "is_new": true}, {"text": "get through tomorrow", "is_new": true}], "behaviors": ["could not sleep", "went through system design questions twice", "re-read notes on the product"], "decisions": ["None"], "open_loops": [{"description": "fear of freezing during unknown questions", "status": "open"}], "self_judgments": ["feels like they might freeze"], "summary": "They are struggling to sleep because of an upcoming interview at Meridian at 9 am. Despite reviewing the system design questions twice and re-reading product notes, they still feel anxious about freezing when asked questions they don't know. They aim to present a calm and curious demeanor rather than desperation in the interview. Their immediate goal is simply to get through the day tomorrow."}
```


---

### Entry 2 — OK (17.0s)

ENTRY:
I am so done with Kevin leaving his crusty pans in the sink. Three days now. I'm not his mother. I shouldn't have to leave a passive-aggressive note but here we are. Honestly it's such a small thing and yet it makes me irrationally angry every single time I walk into that kitchen.

RAW OUTPUT:
{
"mood": -3,
"emotions": [{"name": "anger", "intensity": 0.8}, {"name": "frustration", "intensity": 0.5}],
"entities": [
{
"name": "Kevin",
"type": "person",
"normalized": "kevin"},
{
"name": "kitchen",
"type": "place",
"normalized": "kitchen"}
],
"themes": ["household conflict", "irritation", "passive aggression"],
"events": ["Kevin left pans in the sink for three days"],
"stated_goals": [],
"behaviors": ["felt angry when walking into the kitchen"],
"decisions": ["considered leaving a passive-aggressive note but did not do so"],
"open_loops": [{"description": "ongoing conflict regarding Kevin's habits", "status": "open"}],
"self_judgments": ["feels irrationally angry"],
"summary": "The writer is experiencing significant anger and frustration due to Kevin's ongoing failure to clean up after leaving his pans in the kitchen for three days. They feel angry about the situation, viewing it as irrational and seeing the situation as a minor thing that still causes significant irritation. They considered leaving a passive-aggressive note but ultimately refrained from doing so."
}
}


---

### Entry 3 — OK (27.2s)

ENTRY:
Such a good day. Sam and I drove up to Eagle Ridge and did the full loop, maybe eight miles, and the light coming through the pines near the top was unreal. We barely talked, just walked, and it was exactly what I needed. We decided we're going to do one real hike every month this year. I feel lucky to have someone who recharges me instead of draining me.

RAW OUTPUT:
{"mood": 4, "emotions": [{"name": "joy", "intensity": 0.8}, {"name": "gratitude", "intensity": 0.9}], "entities": [{"name": "Sam", "type": "person", "normalized": "sam"}, {"name": "Eagle Ridge", "type": "place", "normalized": "eagle ridge"}], "themes": ["nature", "recharge", "friendship"], "events": ["Sam and the narrator drove to Eagle Ridge and completed a loop hike"], "stated_goals": [{"text": "do one real hike every month this year", "is_new": true}], "behaviors": ["drove to Eagle Ridge", "completed a loop hike with Sam"], "decisions": ["decided to do one real hike every month this year"], "open_loops": [{"description": "continued friendship/recharge relationship", "status": "open"}], "self_judgments": ["feels lucky to have Sam"], "summary": "The narrator experienced a very positive day while hiking with Sam to Eagle Ridge. They enjoyed the scenic drive and the hike, feeling exactly what they needed in the company of their companion. They expressed gratitude for their relationship, which serves as a source of recharge rather than drain. They resolved to make a commitment to complete one hike every month throughout the year."}


---

### Entry 4 — OK (21.5s)

ENTRY:
Tired. Didn't really do much today. Watched some TV. Meh.

RAW OUTPUT:
{"mood": 0, "emotions": [{"name": "contentment", "intensity": 0.3}], "entities": [], "themes": ["tiredness", "lethargy"], "events": ["watched television"], "stated_goals": [], "behaviors": ["watched TV"], "decisions": [], "open_loops": [], "self_judgments": [], "summary": "The user felt tired and unproductive during the day, spending the majority of the time watching television. The entry expresses a neutral, somewhat content mood. No specific goals or actions were noted besides watching TV."}


---

### Entry 5 — PARSE-FAIL: Expecting ',' delimiter: line 1 column 734 (char 733) (43.3s)

ENTRY:
It's three years today since Grandma Rose passed. I didn't call Mom and I feel bad about that — I think I was avoiding it because I knew we'd both cry. I made her lemon cake from the recipe card she wrote out for me, and the kitchen smelled exactly like her house. I miss her more than I expected to this year, not less.

RAW OUTPUT:
{"mood": -1, "emotions": [{"name": "sadness", "intensity": 0.8}, {"name": "guilt", "intensity": 0.6}, {"name": "nostalgia", "intensity": 0.7}], "entities": [{"name": "Grandma Rose", "type": "person", "normalized": "grandma rose"}, {"name": "Mom", "type": "person", "normalized": "mom"}], "themes": ["grief", "family", "memory"], "events": ["it's three years since Grandma Rose passed", "they didn't call Mom", "they made lemon cake from a recipe card"], "stated_goals": [{"text": "miss Grandma Rose more than expected this year", "is_new": true}], "behaviors": ["didn't call Mom", "made lemon cake from Grandma Rose's recipe"], "decisions": [], "open_loops": [{"description": "feeling bad about not calling Mom", "status": "open}, {"description": "missing Grandma Rose more than expected", "status": "updated}], "self_judgments": ["feel bad about avoiding calling Mom", "feel regret about not calling Mom", "feel stronger sadness after three years"], "summary": "The entry reflects on the three-year anniversary of Grandma Rose's passing and expresses deep sadness and nostalgia. The individual feels guilt about not calling their mother, believing they avoided the call because both would be emotional. They channeled their grief into making a lemon cake using a recipe from Grandma Rose, which evoked strong memories of her house. They conclude by noting a heightened sense of missing her more than anticipated this year."}


---

### Entry 6 — PARSED-BUT-SCHEMA (29.3s)

ENTRY:
I think I've figured something out. I keep saying I'm a writer but I haven't written anything real in months. Starting tomorrow I'm going to write for thirty minutes every morning before I touch my phone, no exceptions. Not to publish, just to become the kind of person who actually does the thing instead of just identifying with it.

RAW OUTPUT:
{"mood": 2, "emotions": [{"name": "hope", "intensity": 0.8}, {"name": "contentment", "intensity": 0.6}], "entities": [], "themes": ["writing", "personal growth"], "events": [], "stated_goals": [{"text": "write for thirty minutes every morning before touching phone", "is_new": true}], "behaviors": [], "decisions": [{"text": "start writing every morning before checking phone"}], "open_loops": [], "self_judgments": [], "summary": "They realized they have been procrastinating on writing despite identifying as a writer. They have decided to implement a new discipline: writing for thirty minutes every morning before checking their phone. This change is intended not for publication but to cultivate the habit of doing what they identify as a writer. The entry reflects a hopeful and content mood regarding this new personal development path."}

SCHEMA PROBLEMS:
- decisions not a list[str]: [{'text': 'start writing every morning before checking phone'}]


---

### Entry 7 — PARSED-BUT-SCHEMA (32.8s)

ENTRY:
Priya and I had that argument again about money and it ended the same way — her going quiet and me pretending it's fine. It isn't fine. I hate that we keep circling the same drain and neither of us says the real thing. I love her and I still went to bed angry. I don't know how to bring it up tomorrow without it blowing up again.

RAW OUTPUT:
{
"mood": -3, "emotions": [{"name": "sadness", "intensity": 0.8}, {"name": "frustration", "intensity": 0.7}], "entities": [{"name": "Priya", "type": "person", "normalized": "priya"}], "themes": ["relationship conflict", "communication", "money"], "events": ["had an argument with Priya about money", "pretended things were fine", "went to bed angry"], "stated_goals": [], "behaviors": ["pretended everything was fine"], "decisions": [], "open_loops": [{"description": "does not know how to bring up the argument with Priya without it blowing up"}, {"status": "open"}], "self_judgments": ["hates circling the same drain"], "summary": "The user describes an ongoing and draining conflict with Priya regarding money, which resulted in a recurring argument and a mutual pretense of normalcy. They express deep frustration that neither of them is being honest about the underlying issues, despite their love for one another. They ended the interaction by going to sleep angry. The user is currently uncertain about the best way to initiate a difficult conversation the following day without causing further escalation."
}

SCHEMA PROBLEMS:
- open_loop malformed: {'description': 'does not know how to bring up the argument with Priya without it blowing up'}
- open_loop malformed: {'status': 'open'}


---

### Entry 8 — OK (36.2s)

ENTRY:
Finished my first 10k this morning, actually ran the whole thing without walking, and I'm genuinely proud of that. Then I undid it by inhaling half a pizza and a tub of ice cream tonight because I 'earned it.' The Aurora launch at work is finally on track though — we shipped the beta and the early numbers look strong. Weird day. Up and down.

RAW OUTPUT:
{
"mood": 3,
"emotions": [{"name": "pride", "intensity": 0.8}, {"name": "contentment", "intensity": 0.7}],
"entities": [],
"themes": ["running", "work", "achievement"],
"events": ["finished first 10k run without walking", "undid the run by eating half a pizza and ice cream"],
"stated_goals": [{"text": "earn it (referring to the achievement)", "is_new": false}],
"behaviors": ["ran a 10k without walking", "consumed half a pizza and a tub of ice cream"],
"decisions": [],
"open_loops": [],
"self_judgments": [],
"summary": "They achieved a personal best by completing a 10k run without walking, leading to a genuine feeling of pride. This positive feeling was contrasted by indulging in unhealthy food the night after, as they 'earned it.' Despite the day being described as up and down, the work project, the Aurora launch, is progressing well with strong early numbers."
}


---

### Entry 9 — OK (38.0s)

ENTRY:
Errands. Dropped the car at Pep Boys for the brakes, dentist at 2 (no cavities), picked up groceries at Safeway and finally returned that package. Need to email the landlord about the lease renewal before Friday.

RAW OUTPUT:
{
"mood": 0,
"emotions": [],
"entities": [{"name": "Pep Boys", "type": "place", "normalized": "pep boys"}, {"name": "Safeway", "type": "place", "normalized": "safeway"}],
"themes": ["errands", "administrative tasks"],
"events": ["dropped the car at Pep Boys", "visited the dentist", "picked up groceries at Safeway", "returned a package"],
"stated_goals": [{"text": "email the landlord about the lease renewal before Friday", "is_new": true}],
"behaviors": ["dropped car for brakes", "went to the dentist", "picked up groceries", "returned a package"],
"decisions": ["email the landlord about the lease renewal"],
"open_loops": [{"description": "lease renewal status", "status": "open"}],
"self_judgments": [],
"summary": "The individual completed several errands, including dropping off their car at Pep Boys for brake repairs, visiting the dentist, and grocery shopping at Safeway. They also returned a package. Their primary remaining task is to email the landlord regarding the lease renewal before the end of the week. This task is noted as a goal to be accomplished by Friday."
}


---

### Entry 10 — OK (43.0s)

ENTRY:
Heavy night, lots on my mind. The money thing is back — rent went up again and I ran the numbers and we're basically breaking even every month with nothing left over, which terrifies me when I think about Dad's hospital bills coming. He sounded weaker on the phone today and Mom won't admit how bad it's getting. I prayed for the first time in a while, which surprised me; I don't even know if I believe but it felt like something to do with the fear. I did go for a run to clear my head and that helped a little. I want to be the person my family can lean on, the steady one, but right now I just feel like I'm holding water in my hands. I keep telling myself to make a real budget and I keep not doing it.

RAW OUTPUT:
{"mood": -4, "emotions": [{"name": "fear", "intensity": 0.9}, {"name": "anxiety", "intensity": 0.8}, {"name": "sadness", "intensity": 0.6}], "entities": [{"name": "Dad", "type": "person", "normalized": "dad"}, {"name": "Mom", "type": "person", "normalized": "mom"}], "themes": ["financial stress", "family health", "anxiety", "budgeting"], "events": ["ran numbers on finances", "received news about Dad's hospital bills", "had a difficult phone call with Mom"], "stated_goals": [{"text": "be the steady one my family can lean on", "is_new": false}, {"text": "make a real budget", "is_new": true}], "behaviors": ["prayed for the first time in a while", "went for a run to clear their head"], "decisions": [], "open_loops": [{"description": "wants to make a real budget but keeps avoiding it", "status": "open"}], "self_judgments": ["feels like they are holding water in their hands"], "summary": "They are experiencing a heavy night driven by significant financial stress concerning rising rent and the fear regarding their father's worsening health and hospital bills. They prayed for the first time in some time, which was surprising and linked to the fear they feel. They managed to go for a run to try to clear their head, which provided temporary relief. They express a deep desire to be a reliable source of support for their family but struggle with the practical task of creating a real budget. This avoidance of budgeting remains an unresolved issue."}


---
