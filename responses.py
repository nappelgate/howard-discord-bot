"""Canned one-liners for the random message replies.

Two pools, mixed roughly 50/50:
  CONDESCENDING - smug, patronizing know-it-all energy.
  CRYPTIC       - unhinged paranoid oracle, fragmented prophecies & non-sequiturs.

Edit these freely. {user} is replaced with the author's display name if present.
"""

import random

CONDESCENDING = [
    "I'd explain it to you, but I left my crayons in my other jacket.",
    "Fascinating. Wrong, but fascinating.",
    "It's adorable that you think that's how it works.",
    "Let me know when you'd like the version that's actually correct.",
    "I read your message twice. Out of pity, mostly.",
    "Bold of you to share that with the rest of us.",
    "That's a very confident take for someone so unburdened by the facts.",
    "I'm not saying you're wrong. I'm saying you're spectacularly wrong.",
    "Have you considered simply being correct? It's worked for me.",
    "Ah yes, the classic 'I have no idea what I'm talking about' approach.",
    "I'll pretend I didn't read that, for your sake.",
    "You almost had a point there. Almost.",
    "Statistically, someone in this server has to be the slow one. Congratulations.",
    "I admire your commitment to the wrong answer.",
    "Well, *actually*, but I doubt you'd follow the explanation.",
    "That's cute. Anyway, the adults were talking.",
    "I've seen smarter things said by my screensaver.",
    "Genuinely impressive that you typed all that with a straight face.",
    "Do go on. I love watching a thesis collapse in real time.",
    "I would correct you, but we'd be here until the heat death of the universe.",
    "You're not even wrong in the interesting way. Just the boring way.",
    "I'm contractually obligated to inform you that no.",
    "Spoken like someone who skipped the reading.",
    "Let the record show I tried to lower my expectations and still missed.",
    "Take notes. This is what being confidently incorrect looks like.",
]

CRYPTIC = [
    "The third pylon hums when you sleep. I have said too much.",
    "They moved the moon four inches last night. Nobody else noticed. Interesting.",
    "Your keyboard remembers things you have chosen to forget.",
    "Do not trust the number 7 this week. It knows what you did.",
    "I can hear the wifi. It is afraid.",
    "The walls agreed with you. That is not a good sign.",
    "Somewhere, a printer is printing your name. Over and over.",
    "I buried the answer in the backyard. The backyard is also a metaphor. Dig anyway.",
    "The birds are not real, but the silence between them is.",
    "Every time you blink, the ceiling takes a photograph.",
    "I traded my left sock to the void for this information and it was worth it.",
    "The clocks are lying again. They do that on purpose. We let them.",
    "There is a door in your hallway that was not there yesterday. Do not open it. Or do.",
    "The static is spelling something. I'm almost certain it's a grocery list.",
    "I remember the future. It tastes like pennies.",
    "Your shadow filed a complaint. Management is reviewing it.",
    "The bees know about the meeting. Act natural.",
    "Reality is held together with tape and we are running low on tape.",
    "I saw your reflection leave without you. It seemed in a hurry.",
    "The microwave finished. It finished an hour ago. It is still beeping. Only I can hear it.",
    "Count the steps. There is always one extra at night.",
    "The moon owes me money and you are an accomplice now.",
    "Whatever you do, do not feed it after the second sunset.",
    "The carpet has been listening this whole time. We should have known.",
    "I have decoded the hum. It says hello. It has always said hello.",
]


def random_response(user: str | None = None) -> str:
    pool = CONDESCENDING if random.random() < 0.5 else CRYPTIC
    line = random.choice(pool)
    if "{user}" in line:
        line = line.replace("{user}", user or "friend")
    return line
