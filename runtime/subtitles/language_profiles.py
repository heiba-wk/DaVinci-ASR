from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

SUPPORTED_LANGUAGES = (
    "Chinese",
    "Cantonese",
    "English",
    "French",
    "German",
    "Italian",
    "Japanese",
    "Korean",
    "Portuguese",
    "Russian",
    "Spanish",
)
NO_SPACE_LANGUAGES = frozenset({"Chinese", "Cantonese", "Japanese"})
LANGUAGE_CODES = {
    "Chinese": "zh",
    "Cantonese": "yue",
    "English": "en",
    "French": "fr",
    "German": "de",
    "Italian": "it",
    "Japanese": "ja",
    "Korean": "ko",
    "Portuguese": "pt",
    "Russian": "ru",
    "Spanish": "es",
}
_NUMBER = re.compile(
    r"^[+-]?(?:\d+(?:[.,]\d+)*|[零〇一二两兩三四五六七八九十百千万萬亿億]+)$"
)


def normalize_lexical_unit(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value)).casefold().strip()


def _units(value: str) -> tuple[str, ...]:
    return tuple(normalize_lexical_unit(item) for item in value.split() if item.strip())


def _phrases(*values: str) -> tuple[tuple[str, ...], ...]:
    return tuple(_units(value) for value in values)


def _words(value: str) -> frozenset[str]:
    return frozenset(_units(value))


@dataclass(frozen=True, slots=True)
class LanguageProfile:
    language: str
    code: str
    conjunctions: frozenset[str]
    subordinates: frozenset[str]
    prepositions: frozenset[str]
    boundary_phrases: tuple[tuple[str, ...], ...]
    protected_left: frozenset[str]
    protected_right: frozenset[str]
    protected_pairs: frozenset[tuple[str, str]]
    protected_phrases: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        for phrase in self.boundary_phrases + self.protected_phrases:
            if not 2 <= len(phrase) <= 4:
                raise ValueError(
                    f"{self.language} profile phrases must contain 2-4 lexical units"
                )


@dataclass(frozen=True, slots=True)
class LanguageBoundaryAnalysis:
    before_conjunction: bool = False
    before_subordinate: bool = False
    before_preposition: bool = False
    boundary_phrase: bool = False
    protected_boundary: bool = False
    protected_phrase: bool = False
    matched_boundary_phrase: tuple[str, ...] = ()
    matched_protected_phrase: tuple[str, ...] = ()
    protection_reasons: tuple[str, ...] = ()


def _pair_set(*values: tuple[str, str]) -> frozenset[tuple[str, str]]:
    return frozenset(
        (normalize_lexical_unit(left), normalize_lexical_unit(right))
        for left, right in values
    )


ENGLISH_PHRASES = _phrases(
    "because of",
    "in order to",
    "as well as",
    "even though",
    "rather than",
    "such as",
)
FRENCH_PHRASES = _phrases(
    "parce que",
    "afin de",
    "ainsi que",
    "même si",
    "alors que",
    "au lieu de",
)
GERMAN_PHRASES = _phrases(
    "nicht nur",
    "als auch",
    "auch wenn",
    "so dass",
    "anstatt zu",
    "im gegensatz zu",
    "sowohl als auch",
)
ITALIAN_PHRASES = _phrases(
    "anche se",
    "a causa di",
    "in modo da",
    "invece di",
    "così come",
)
PORTUGUESE_PHRASES = _phrases(
    "por causa de",
    "a fim de",
    "assim como",
    "no entanto",
    "por isso",
)
RUSSIAN_PHRASES = _phrases(
    "потому что",
    "так как",
    "для того чтобы",
    "несмотря на",
    "а также",
)
SPANISH_PHRASES = _phrases(
    "sin embargo",
    "por lo tanto",
    "por lo que",
    "por causa de",
    "para que",
    "a pesar de",
)
CHINESE_PHRASES = _phrases(
    "为了 让",
    "而 不是",
    "不仅 要",
    "与 此 同时",
    "只 要",
    "即 使",
)
CANTONESE_PHRASES = _phrases(
    "唔 單止",
    "而 且",
    "只 要",
    "即 使",
    "另 一方面",
)
JAPANESE_PHRASES = _phrases(
    "その ため",
    "だけ で なく",
    "に も かかわらず",
    "と いう",
    "ため に",
)
JAPANESE_PROTECTED_PHRASES = JAPANESE_PHRASES + _phrases(
    "*う よう に",
    "なけれ ば なら ない",
)
KOREAN_PHRASES = _phrases(
    "그렇기 때문에",
    "*뿐만 아니라",
    "*하기 위해*",
    "그럼에도 불구하고",
    "반면 에",
)
KOREAN_CLAUSE_ENDINGS = (
    "지만",
    "는데",
    "은데",
    "ㄴ데",
    "거나",
    "므로",
    "으며",
    "면서",
)


LANGUAGE_PROFILES = {
    "Chinese": LanguageProfile(
        language="Chinese",
        code="zh",
        conjunctions=_words(
            "而且 或者 但是 然后 所以 因此 并且 不过 可是 同时 另外 此外 然而"
        ),
        subordinates=_words("因为 如果 虽然 尽管 由于 即使 除非 一旦 只要 为了 以便"),
        prepositions=_words("从 在 向 对 为 以 由 把 被 跟 和 与 给 关于 通过"),
        boundary_phrases=CHINESE_PHRASES,
        protected_left=_words(
            "的 地 得 之 不 没 没有 未 无 别 从 在 向 对 为 以 由 把 被 跟 和 与 给 我 你 他 她 它 我们 你们 他们 这个 那个 哪个 每个 各个 某个 整个 这些 那些 所有 任何 一个 两个 三个"
        ),
        protected_right=_words(
            "的 地 得 们 了 着 过 什么 个 台 位 名 件 条 张 本 套 种 项 家 座 辆 部 份 批 组 对 双 元 秒 分钟 小时 天 年 月 周 次 倍 米 公里 厘米 毫米 克 千克 公斤 升 毫升 度 人 页 行 列 层 级 百分比"
        ),
        protected_pairs=_pair_set(
            ("不仅", "而且"),
            ("虽然", "但是"),
            ("如果", "就"),
            ("因为", "所以"),
        ),
        protected_phrases=CHINESE_PHRASES,
    ),
    "Cantonese": LanguageProfile(
        language="Cantonese",
        code="yue",
        conjunctions=_words(
            "而且 或者 但係 跟住 所以 因此 並且 不過 可是 同時 另外 否則"
        ),
        subordinates=_words("因為 如果 雖然 儘管 由於 即使 除非 一旦 只要 為咗 以便"),
        prepositions=_words("喺 向 對 由 畀 同 跟 關於 經過"),
        boundary_phrases=CANTONESE_PHRASES,
        protected_left=_words(
            "嘅 地 得 唔 未 冇 喺 向 對 由 畀 同 我 你 佢 我哋 你哋 佢哋 呢個 嗰個 邊個 每個 各個 某個 成個 呢啲 嗰啲 所有 任何 一個 兩個 三個"
        ),
        protected_right=_words(
            "嘅 地 得 哋 咗 緊 住 過 晒 咩 乜 邊 边 個 位 名 件 條 張 本 套 種 項 部 份 批 組 對 雙 蚊 秒 分鐘 小時 日 年 月 周 次 倍 米 公里 克 公斤 升 毫升 度 人 頁 行 層 級 百分比"
        ),
        protected_pairs=_pair_set(
            ("唔單止", "而且"),
            ("雖然", "但係"),
            ("如果", "就"),
            ("因為", "所以"),
        ),
        protected_phrases=CANTONESE_PHRASES,
    ),
    "English": LanguageProfile(
        language="English",
        code="en",
        conjunctions=_words(
            "and or but nor yet so however therefore moreover furthermore nevertheless meanwhile"
        ),
        subordinates=_words(
            "because when if although while after before since unless until where that whether once whenever wherever"
        ),
        prepositions=_words(
            "about above across after against among around at before behind below between by despite during for from in inside into near of on onto over through to toward under until with within without"
        ),
        boundary_phrases=ENGLISH_PHRASES,
        protected_left=_words(
            "a an the this that these those my your his her its our their i you he she it we they am are is was were be been being can could do does did has have had may might must shall should will would not no never to of in on at for from with by"
        ),
        protected_right=_words(
            "percent percentage dollars euros pounds grams kilograms milligrams metres meters kilometres kilometers seconds minutes hours days weeks months years"
        ),
        protected_pairs=_pair_set(
            ("not", "only"),
            ("rather", "than"),
            ("such", "as"),
            ("either", "or"),
            ("neither", "nor"),
        ),
        protected_phrases=ENGLISH_PHRASES,
    ),
    "French": LanguageProfile(
        language="French",
        code="fr",
        conjunctions=_words(
            "et ou mais donc pourtant cependant néanmoins puis ainsi toutefois"
        ),
        subordinates=_words("parce puisque quand si quoique lorsque tandis comme afin"),
        prepositions=_words(
            "à après avant avec chez contre dans de depuis derrière devant durant entre envers par parmi pendant pour sans sous sur vers"
        ),
        boundary_phrases=FRENCH_PHRASES,
        protected_left=_words(
            "le la les un une des ce cet cette ces mon ma mes ton ta tes son sa ses notre nos votre vos leur leurs je tu il elle nous vous ils elles ne pas suis es est sommes êtes sont étais était avons avez ont ai as a peut peuvent doit doivent va vont de à pour dans sur avec sans"
        ),
        protected_right=_words(
            "pour cent euros grammes kilogrammes milligrammes mètres kilomètres secondes minutes heures jours semaines mois années"
        ),
        protected_pairs=_pair_set(
            ("ne", "pas"),
            ("plus", "que"),
            ("moins", "que"),
            ("tel", "que"),
        ),
        protected_phrases=FRENCH_PHRASES,
    ),
    "German": LanguageProfile(
        language="German",
        code="de",
        conjunctions=_words(
            "und oder aber denn sondern doch deshalb daher trotzdem außerdem währenddessen"
        ),
        subordinates=_words(
            "weil wenn obwohl während nachdem bevor seit falls bis dass ob sobald sofern"
        ),
        prepositions=_words(
            "ab an auf aus bei bis durch für gegen hinter in mit nach neben ohne seit über um unter von vor zu zwischen"
        ),
        boundary_phrases=GERMAN_PHRASES,
        protected_left=_words(
            "der die das den dem des ein eine einen einem einer eines dieser diese dieses mein meine dein deine sein seine ihr ihre unser unsere ich du er sie es wir ihr nicht kein keine bin bist ist sind seid war waren kann können muss müssen soll sollen wird werden würde haben hat hatte an auf aus bei für in mit nach von vor zu"
        ),
        protected_right=_words(
            "prozent euro gramm kilogramm milligramm meter kilometer sekunden minuten stunden tage wochen monate jahre januar februar märz april mai juni juli august september oktober november dezember"
        ),
        protected_pairs=_pair_set(
            ("nicht", "nur"),
            ("sowohl", "als"),
            ("weder", "noch"),
            ("mehr", "als"),
        ),
        protected_phrases=GERMAN_PHRASES,
    ),
    "Italian": LanguageProfile(
        language="Italian",
        code="it",
        conjunctions=_words(
            "e o ma quindi però tuttavia inoltre pertanto altrimenti intanto"
        ),
        subordinates=_words(
            "perché quando se sebbene mentre dopo prima finché dove che qualora"
        ),
        prepositions=_words(
            "a al allo alla ai agli alle da dal dallo dalla dai dagli dalle di del dello della dei degli delle in nel nello nella nei negli nelle con su sul sullo sulla sui sugli sulle per tra fra contro durante senza verso oltre sotto sopra"
        ),
        boundary_phrases=ITALIAN_PHRASES,
        protected_left=_words(
            "il lo la i gli le un uno una questo questa questi queste mio mia miei mie tuo tua suoi sue io tu lui lei noi voi loro non mai è sono era erano essere può possono deve devono ha hanno aveva a da di in con su per tra fra"
        ),
        protected_right=_words(
            "per cento euro grammi chilogrammi milligrammi metri chilometri secondi minuti ore giorni settimane mesi anni"
        ),
        protected_pairs=_pair_set(
            ("non", "solo"),
            ("sia", "sia"),
            ("più", "di"),
            ("meno", "di"),
        ),
        protected_phrases=ITALIAN_PHRASES,
    ),
    "Japanese": LanguageProfile(
        language="Japanese",
        code="ja",
        conjunctions=_words(
            "そして また しかし けれど それで だから でも ただ なお 一方"
        ),
        subordinates=_words("から ので けれど なら たら ても のに ように ために"),
        prepositions=frozenset(),
        boundary_phrases=JAPANESE_PHRASES,
        protected_left=_words("の と 的 ない ぬ ません です ます"),
        protected_right=_words(
            "は が を に へ と で から まで より の も や か 的 た て だ ない ぬ ます です できる できない 値 台 人 個 枚 本 円 秒 分 時 時間 日 週 月 年 パーセント キログラム ミリグラム メートル キロメートル"
        ),
        protected_pairs=_pair_set(
            ("だけ", "で"),
            ("で", "なく"),
            ("に", "よって"),
            ("と", "いう"),
        ),
        protected_phrases=JAPANESE_PROTECTED_PHRASES,
    ),
    "Korean": LanguageProfile(
        language="Korean",
        code="ko",
        conjunctions=_words("그리고 또는 하지만 그래서 그러나 그런데 또한 한편 따라서"),
        subordinates=_words("때문에 만약 비록 동안 이후 이전 경우 때까지"),
        prepositions=frozenset(),
        boundary_phrases=KOREAN_PHRASES,
        protected_left=_words(
            "은 는 이 가 을 를 에 에서 에게 와 과 로 으로 도 만 의 안 못 않다 없다 있다 하다 된다 합니다"
        ),
        protected_right=_words(
            "은 는 이 가 을 를 에 에서 에게 와 과 로 으로 도 만 개 명 대 원 초 분 시간 일 주 달 년 퍼센트 그램 킬로그램 밀리그램 미터 킬로미터"
        ),
        protected_pairs=_pair_set(
            ("뿐만", "아니라"),
            ("그렇기", "때문에"),
            ("할", "수"),
            ("수", "없다"),
        ),
        protected_phrases=KOREAN_PHRASES,
    ),
    "Portuguese": LanguageProfile(
        language="Portuguese",
        code="pt",
        conjunctions=_words(
            "e ou mas portanto porém contudo além disso entretanto assim senão enquanto"
        ),
        subordinates=_words(
            "porque quando se embora enquanto depois antes desde até onde que caso"
        ),
        prepositions=_words(
            "a após até com contra de desde durante em entre para perante por sem sob sobre trás"
        ),
        boundary_phrases=PORTUGUESE_PHRASES,
        protected_left=_words(
            "o a os as um uma uns umas este esta estes estas meu minha meus minhas seu sua seus suas eu tu ele ela nós vocês eles elas não nunca é são era eram ser pode podem deve devem tem têm tinha a com de em para por sem sobre"
        ),
        protected_right=_words(
            "por cento reais euros gramas quilogramas miligramas metros quilômetros segundos minutos horas dias semanas meses anos vez vezes"
        ),
        protected_pairs=_pair_set(
            ("não", "só"),
            ("tanto", "quanto"),
            ("mais", "do"),
            ("menos", "do"),
        ),
        protected_phrases=PORTUGUESE_PHRASES,
    ),
    "Russian": LanguageProfile(
        language="Russian",
        code="ru",
        conjunctions=_words(
            "и или но зато однако поэтому следовательно кроме того между тем иначе"
        ),
        subordinates=_words(
            "потому поскольку когда если хотя пока после прежде чем чтобы что ли раз"
        ),
        prepositions=_words(
            "без в до для за из к кроме между на над о от перед по под при про с через у"
        ),
        boundary_phrases=RUSSIAN_PHRASES,
        protected_left=_words(
            "этот эта это эти мой моя мои твой твоя его её наш наша ваш ваша я ты он она оно мы вы они не ни был была были быть может могут должен должна должны имеет имеют в на к с по от до для без при"
        ),
        protected_right=_words(
            "процентов рубля рублей евро граммов килограммов миллиграммов метров километров секунд минут часов дней недель месяцев лет"
        ),
        protected_pairs=_pair_set(
            ("не", "только"),
            ("как", "так"),
            ("ни", "ни"),
            ("более", "чем"),
        ),
        protected_phrases=RUSSIAN_PHRASES,
    ),
    "Spanish": LanguageProfile(
        language="Spanish",
        code="es",
        conjunctions=_words(
            "y o pero sino aunque por tanto además mientras entretanto tampoco"
        ),
        subordinates=_words(
            "porque cuando si aunque mientras después antes desde hasta donde que salvo"
        ),
        prepositions=_words(
            "a ante bajo con contra de desde durante en entre hacia hasta mediante para por según sin sobre tras"
        ),
        boundary_phrases=SPANISH_PHRASES,
        protected_left=_words(
            "el la los las un una unos unas este esta estos estas mi mis tu tus su sus yo tú él ella nosotros ustedes ellos ellas no nunca es son era eran ser puede pueden debe deben ha han había a con de en para por sin sobre"
        ),
        protected_right=_words(
            "por ciento pesos euros gramos kilogramos miligramos metros kilómetros segundos minutos horas días semanas meses años vez veces"
        ),
        protected_pairs=_pair_set(
            ("no", "solo"),
            ("tanto", "como"),
            ("más", "que"),
            ("menos", "que"),
        ),
        protected_phrases=SPANISH_PHRASES,
    ),
}


def get_language_profile(language: str) -> LanguageProfile:
    try:
        return LANGUAGE_PROFILES[language]
    except KeyError as exc:
        raise ValueError(f"Unsupported subtitle language profile: {language}") from exc


def _phrase_at(
    lexical_units: tuple[str, ...],
    start: int,
    phrases: tuple[tuple[str, ...], ...],
) -> tuple[str, ...]:
    if start < 0 or start >= len(lexical_units):
        return ()
    for phrase in phrases:
        if _matches_phrase(lexical_units[start : start + len(phrase)], phrase):
            return phrase
    return ()


def _phrase_crossing(
    lexical_units: tuple[str, ...],
    boundary_index: int,
    phrases: tuple[tuple[str, ...], ...],
) -> tuple[str, ...]:
    for phrase in phrases:
        for split in range(1, len(phrase)):
            start = boundary_index - split
            if start < 0:
                continue
            if _matches_phrase(lexical_units[start : start + len(phrase)], phrase):
                return phrase
    return ()


def _matches_phrase(
    actual: tuple[str, ...],
    pattern: tuple[str, ...],
) -> bool:
    if len(actual) != len(pattern):
        return False
    for value, expected in zip(actual, pattern, strict=True):
        prefix_wildcard = expected.startswith("*")
        suffix_wildcard = expected.endswith("*")
        literal = expected.strip("*")
        if prefix_wildcard and suffix_wildcard:
            matches = literal in value
        elif prefix_wildcard:
            matches = value.endswith(literal)
        elif suffix_wildcard:
            matches = value.startswith(literal)
        else:
            matches = value == literal
        if not matches:
            return False
    return True


def analyze_language_boundary(
    lexical_units: tuple[str, ...],
    boundary_index: int,
    profile: LanguageProfile,
) -> LanguageBoundaryAnalysis:
    normalized = tuple(normalize_lexical_unit(unit) for unit in lexical_units)
    left = normalized[boundary_index - 1] if boundary_index > 0 else ""
    right = normalized[boundary_index] if boundary_index < len(normalized) else ""
    boundary_phrase = _phrase_at(normalized, boundary_index, profile.boundary_phrases)
    korean_clause_ending = bool(
        profile.language == "Korean"
        and left
        and left.endswith(KOREAN_CLAUSE_ENDINGS)
    )
    protected_phrase = _phrase_crossing(
        normalized, boundary_index, profile.protected_phrases
    )
    reasons: list[str] = []
    if left in profile.protected_left:
        reasons.append("protected_left")
    if left in profile.prepositions:
        reasons.append("preposition_object")
    if left in profile.subordinates:
        reasons.append("subordinate_clause")
    if left in profile.conjunctions:
        reasons.append("conjunction_complement")
    if right in profile.protected_right:
        reasons.append("protected_right")
    if (left, right) in profile.protected_pairs:
        reasons.append("protected_pair")
    if protected_phrase:
        reasons.append("protected_phrase")
    if _NUMBER.fullmatch(left) and right in profile.protected_right:
        reasons.append("number_unit")
    return LanguageBoundaryAnalysis(
        before_conjunction=right in profile.conjunctions,
        before_subordinate=right in profile.subordinates,
        before_preposition=right in profile.prepositions,
        boundary_phrase=bool(boundary_phrase or korean_clause_ending),
        protected_boundary=bool(reasons),
        protected_phrase=bool(protected_phrase),
        matched_boundary_phrase=(left,) if korean_clause_ending else boundary_phrase,
        matched_protected_phrase=protected_phrase,
        protection_reasons=tuple(dict.fromkeys(reasons)),
    )


def is_protected_boundary(
    lexical_units: tuple[str, ...],
    boundary_index: int,
    profile: LanguageProfile,
) -> bool:
    """Return the unified soft grammar/phrase protection decision."""
    return analyze_language_boundary(
        lexical_units, boundary_index, profile
    ).protected_boundary
