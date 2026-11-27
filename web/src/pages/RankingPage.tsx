import { ArrowUpRight } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { Link } from '../router';
import type { FindingItem } from '../types';

export type Lang = 'en' | 'hi' | 'bn' | 'ta' | 'te' | 'mr';

interface Strings {
  name: string;
  locale: string;
  eyebrow: string;
  title: string;
  lede: string;
  notAccusation: string;
  sortAmount: string;
  sortRecent: string;
  allIndia: string;
  state: string;
  amount: string;
  evidence: string;
  loadMore: string;
  loading: string;
  empty: string;
  namesNote: string;
  count: string;
  seeAll: string;
  crore: string;
  lakh: string;
  belowBE: string;
  revisedBelow: string;
  singleTeacher: string;
}

const STRINGS: Record<Lang, Strings> = {
  en: {
    name: 'English',
    locale: 'en-IN',
    eyebrow: 'Confirmed discrepancies',
    title: 'The largest gaps found in public spending records',
    lede: 'Each item was found in an official government document and confirmed through independent review. Ranked by the amount involved.',
    notAccusation:
      'A discrepancy is a figure that needs explaining, not proof of wrongdoing. Each one links to its source and lists innocent explanations.',
    sortAmount: 'Largest amount',
    sortRecent: 'Most recent',
    allIndia: 'All of India',
    state: 'State',
    amount: 'Amount involved',
    evidence: 'Evidence and RTI draft',
    loadMore: 'Show more',
    loading: 'Loading…',
    empty: 'No confirmed discrepancies yet.',
    namesNote: 'Names of schemes and departments are shown as they appear in official records.',
    count: '{n} confirmed discrepancies',
    seeAll: 'See the full ranking',
    crore: 'crore',
    lakh: 'lakh',
    belowBE: 'Actual spending in {year} was {pct}% below the budget estimate',
    revisedBelow: 'The revised estimate for {year} was {pct}% below the budget estimate',
    singleTeacher: '{n} schools run by a single teacher ({pct}% of schools)',
  },
  hi: {
    name: 'हिन्दी',
    locale: 'hi-IN',
    eyebrow: 'पुष्ट विसंगतियाँ',
    title: 'सरकारी खर्च के रिकॉर्ड में मिली सबसे बड़ी विसंगतियाँ',
    lede: 'हर विसंगति किसी आधिकारिक सरकारी दस्तावेज़ में मिली है और स्वतंत्र जाँचों ने उसकी पुष्टि की है। सूची शामिल राशि के क्रम में है।',
    notAccusation:
      'विसंगति का मतलब है ऐसा आँकड़ा जिसकी व्याख्या ज़रूरी है, गड़बड़ी का सबूत नहीं। हर प्रविष्टि के साथ उसका स्रोत और संभावित सामान्य कारण दिए गए हैं।',
    sortAmount: 'सबसे बड़ी राशि',
    sortRecent: 'सबसे नई',
    allIndia: 'पूरा भारत',
    state: 'राज्य',
    amount: 'शामिल राशि',
    evidence: 'सबूत और RTI मसौदा',
    loadMore: 'और दिखाएँ',
    loading: 'लोड हो रहा है…',
    empty: 'अभी कोई पुष्ट विसंगति नहीं है।',
    namesNote: 'योजनाओं और विभागों के नाम आधिकारिक रिकॉर्ड के अनुसार अंग्रेज़ी में दिखाए गए हैं।',
    count: '{n} पुष्ट विसंगतियाँ',
    seeAll: 'पूरी सूची देखें',
    crore: 'करोड़',
    lakh: 'लाख',
    belowBE: '{year} में वास्तविक खर्च बजट अनुमान से {pct}% कम रहा',
    revisedBelow: '{year} का संशोधित अनुमान बजट अनुमान से {pct}% कम रहा',
    singleTeacher: 'एक ही शिक्षक वाले {n} स्कूल ({pct}% स्कूल)',
  },
  bn: {
    name: 'বাংলা',
    locale: 'bn-IN',
    eyebrow: 'নিশ্চিত অসঙ্গতি',
    title: 'সরকারি ব্যয়ের নথিতে পাওয়া সবচেয়ে বড় অসঙ্গতিগুলি',
    lede: 'প্রতিটি অসঙ্গতি একটি সরকারি নথিতে পাওয়া গেছে এবং স্বাধীন যাচাইয়ে নিশ্চিত হয়েছে। সংশ্লিষ্ট অর্থের পরিমাণ অনুযায়ী সাজানো।',
    notAccusation:
      'অসঙ্গতি মানে এমন একটি সংখ্যা যার ব্যাখ্যা প্রয়োজন, অনিয়মের প্রমাণ নয়। প্রতিটির সঙ্গে উৎস এবং সম্ভাব্য সাধারণ কারণ দেওয়া আছে।',
    sortAmount: 'সবচেয়ে বেশি অর্থ',
    sortRecent: 'সাম্প্রতিক',
    allIndia: 'সমগ্র ভারত',
    state: 'রাজ্য',
    amount: 'সংশ্লিষ্ট অর্থ',
    evidence: 'প্রমাণ ও RTI খসড়া',
    loadMore: 'আরও দেখান',
    loading: 'লোড হচ্ছে…',
    empty: 'এখনও কোনো নিশ্চিত অসঙ্গতি নেই।',
    namesNote: 'প্রকল্প ও বিভাগের নাম সরকারি নথি অনুযায়ী ইংরেজিতে দেখানো হয়েছে।',
    count: '{n}টি নিশ্চিত অসঙ্গতি',
    seeAll: 'পুরো তালিকা দেখুন',
    crore: 'কোটি',
    lakh: 'লাখ',
    belowBE: '{year} সালে প্রকৃত ব্যয় বাজেট অনুমানের চেয়ে {pct}% কম ছিল',
    revisedBelow: '{year} সালের সংশোধিত অনুমান বাজেট অনুমানের চেয়ে {pct}% কম ছিল',
    singleTeacher: 'মাত্র একজন শিক্ষকের {n}টি বিদ্যালয় ({pct}% বিদ্যালয়)',
  },
  ta: {
    name: 'தமிழ்',
    locale: 'ta-IN',
    eyebrow: 'உறுதிசெய்யப்பட்ட முரண்பாடுகள்',
    title: 'அரசு செலவுப் பதிவுகளில் கண்டறியப்பட்ட மிகப்பெரிய முரண்பாடுகள்',
    lede: 'ஒவ்வொரு முரண்பாடும் அதிகாரப்பூர்வ அரசு ஆவணத்தில் கண்டறியப்பட்டு, சுயாதீன சரிபார்ப்புகளால் உறுதிசெய்யப்பட்டது. தொடர்புடைய தொகையின் அடிப்படையில் வரிசைப்படுத்தப்பட்டுள்ளது.',
    notAccusation:
      'முரண்பாடு என்பது விளக்கம் தேவைப்படும் ஒரு எண்ணிக்கை; முறைகேட்டுக்கான ஆதாரம் அல்ல. ஒவ்வொன்றுக்கும் அதன் மூலமும் சாத்தியமான இயல்பான காரணங்களும் தரப்பட்டுள்ளன.',
    sortAmount: 'அதிகத் தொகை',
    sortRecent: 'சமீபத்தியவை',
    allIndia: 'முழு இந்தியா',
    state: 'மாநிலம்',
    amount: 'தொடர்புடைய தொகை',
    evidence: 'ஆதாரம் மற்றும் RTI வரைவு',
    loadMore: 'மேலும் காட்டு',
    loading: 'ஏற்றப்படுகிறது…',
    empty: 'இதுவரை உறுதிசெய்யப்பட்ட முரண்பாடுகள் இல்லை.',
    namesNote: 'திட்டங்கள் மற்றும் துறைகளின் பெயர்கள் அதிகாரப்பூர்வ பதிவுகளில் உள்ளபடி ஆங்கிலத்தில் காட்டப்படுகின்றன.',
    count: '{n} உறுதிசெய்யப்பட்ட முரண்பாடுகள்',
    seeAll: 'முழுப் பட்டியலைப் பார்க்க',
    crore: 'கோடி',
    lakh: 'லட்சம்',
    belowBE: '{year} இல் உண்மையான செலவு பட்ஜெட் மதிப்பீட்டை விட {pct}% குறைவு',
    revisedBelow: '{year} க்கான திருத்திய மதிப்பீடு பட்ஜெட் மதிப்பீட்டை விட {pct}% குறைவு',
    singleTeacher: 'ஒரே ஆசிரியர் உள்ள {n} பள்ளிகள் (பள்ளிகளில் {pct}%)',
  },
  te: {
    name: 'తెలుగు',
    locale: 'te-IN',
    eyebrow: 'నిర్ధారించిన వ్యత్యాసాలు',
    title: 'ప్రభుత్వ వ్యయ రికార్డుల్లో కనుగొన్న అతిపెద్ద వ్యత్యాసాలు',
    lede: 'ప్రతి వ్యత్యాసం అధికారిక ప్రభుత్వ పత్రంలో కనుగొనబడింది, స్వతంత్ర తనిఖీల ద్వారా నిర్ధారించబడింది. సంబంధిత మొత్తం ఆధారంగా క్రమంలో ఉంచబడింది.',
    notAccusation:
      'వ్యత్యాసం అంటే వివరణ అవసరమైన ఒక సంఖ్య మాత్రమే, అక్రమానికి రుజువు కాదు. ప్రతిదానికీ దాని మూలం, సాధ్యమైన సాధారణ కారణాలు ఇవ్వబడ్డాయి.',
    sortAmount: 'అత్యధిక మొత్తం',
    sortRecent: 'తాజావి',
    allIndia: 'మొత్తం భారతదేశం',
    state: 'రాష్ట్రం',
    amount: 'సంబంధిత మొత్తం',
    evidence: 'ఆధారాలు, RTI ముసాయిదా',
    loadMore: 'మరిన్ని చూపించు',
    loading: 'లోడ్ అవుతోంది…',
    empty: 'ఇంకా నిర్ధారించిన వ్యత్యాసాలు లేవు.',
    namesNote: 'పథకాలు, శాఖల పేర్లు అధికారిక రికార్డుల్లో ఉన్నట్లుగా ఆంగ్లంలో చూపబడ్డాయి.',
    count: '{n} నిర్ధారించిన వ్యత్యాసాలు',
    seeAll: 'పూర్తి జాబితా చూడండి',
    crore: 'కోట్లు',
    lakh: 'లక్షలు',
    belowBE: '{year}లో వాస్తవ వ్యయం బడ్జెట్ అంచనా కంటే {pct}% తక్కువ',
    revisedBelow: '{year} సవరించిన అంచనా బడ్జెట్ అంచనా కంటే {pct}% తక్కువ',
    singleTeacher: 'ఒకే ఉపాధ్యాయుడు ఉన్న {n} పాఠశాలలు ({pct}% పాఠశాలలు)',
  },
  mr: {
    name: 'मराठी',
    locale: 'mr-IN',
    eyebrow: 'निश्चित विसंगती',
    title: 'सरकारी खर्चाच्या नोंदींमध्ये आढळलेल्या सर्वात मोठ्या विसंगती',
    lede: 'प्रत्येक विसंगती अधिकृत सरकारी दस्तऐवजात आढळली असून स्वतंत्र तपासण्यांनी तिची पुष्टी केली आहे. संबंधित रकमेनुसार क्रम लावला आहे.',
    notAccusation:
      'विसंगती म्हणजे स्पष्टीकरण आवश्यक असलेला आकडा, गैरप्रकाराचा पुरावा नव्हे. प्रत्येकासोबत तिचा स्रोत आणि संभाव्य सामान्य कारणे दिली आहेत.',
    sortAmount: 'सर्वात मोठी रक्कम',
    sortRecent: 'सर्वात अलीकडील',
    allIndia: 'संपूर्ण भारत',
    state: 'राज्य',
    amount: 'संबंधित रक्कम',
    evidence: 'पुरावा आणि RTI मसुदा',
    loadMore: 'आणखी दाखवा',
    loading: 'लोड होत आहे…',
    empty: 'अद्याप कोणतीही निश्चित विसंगती नाही.',
    namesNote: 'योजना आणि विभागांची नावे अधिकृत नोंदींप्रमाणे इंग्रजीत दाखवली आहेत.',
    count: '{n} निश्चित विसंगती',
    seeAll: 'संपूर्ण यादी पहा',
    crore: 'कोटी',
    lakh: 'लाख',
    belowBE: '{year} मध्ये प्रत्यक्ष खर्च अर्थसंकल्पीय अंदाजापेक्षा {pct}% कमी होता',
    revisedBelow: '{year} चा सुधारित अंदाज अर्थसंकल्पीय अंदाजापेक्षा {pct}% कमी होता',
    singleTeacher: 'एकच शिक्षक असलेल्या {n} शाळा ({pct}% शाळा)',
  },
};

const LANGS = Object.keys(STRINGS) as Lang[];
const LANG_KEY = 'aw.lang';
const PAGE = 25;

function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_, k: string) => values[k] ?? '');
}

export function readLang(): Lang {
  try {
    const fromUrl = new URLSearchParams(window.location.search).get('lang');
    if (fromUrl && (LANGS as string[]).includes(fromUrl)) return fromUrl as Lang;
    const saved = window.localStorage.getItem(LANG_KEY);
    if (saved && (LANGS as string[]).includes(saved)) return saved as Lang;
  } catch {
    return 'en';
  }
  return 'en';
}

function saveLang(lang: Lang) {
  try {
    window.localStorage.setItem(LANG_KEY, lang);
  } catch {
    return;
  }
}

function money(amount: number | null | undefined, t: Strings): string {
  if (!amount) return '—';
  const n = Math.abs(amount);
  if (n >= 1e7) return `₹${(n / 1e7).toLocaleString('en-IN', { maximumFractionDigits: 2 })} ${t.crore}`;
  if (n >= 1e5) return `₹${(n / 1e5).toLocaleString('en-IN', { maximumFractionDigits: 2 })} ${t.lakh}`;
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

function describe(f: FindingItem, t: Strings): { who: string; what: string; dept: string } {
  const title = (f.title || '').trim();
  const cut = title.indexOf(': ');
  let who = cut > 0 ? title.slice(0, cut) : f.entity_name || title;
  let what = cut > 0 ? title.slice(cut + 2) : '';
  let dept = '';
  const m = who.match(/^(.*\S)\s*\(((?:Department|Ministry|Dept)[^()]*)\)\s*$/i);
  if (m) {
    who = m[1];
    dept = m[2];
  }
  const below = what.match(/^actual expenditure in (\S+) was ([\d.]+)% below the budget estimate$/i);
  const revised = what.match(/^revised estimate for (\S+) was ([\d.]+)% below the budget estimate$/i);
  const schools = what.match(/^([\d,]+) single-teacher schools \(([\d.]+)% concentration\)$/i);
  if (below) what = fill(t.belowBE, { year: below[1], pct: below[2] });
  else if (revised) what = fill(t.revisedBelow, { year: revised[1], pct: revised[2] });
  else if (schools) what = fill(t.singleTeacher, { n: schools[1], pct: schools[2] });
  else if (what) what = what.charAt(0).toUpperCase() + what.slice(1);
  return { who, what, dept };
}

function fiscal(fy?: string): string {
  if (!fy) return '';
  return /^fy/i.test(fy) ? fy.replace(/^fy\s*/i, 'FY ') : `FY ${fy}`;
}

function RankRow({
  f,
  rank,
  t,
  onSelect,
}: {
  f: FindingItem;
  rank: number;
  t: Strings;
  onSelect: (id: string | number) => void;
}) {
  const { who, what, dept } = describe(f, t);
  const meta = [dept, f.jurisdiction, fiscal(f.fiscal_year)].filter(Boolean).join(' · ');
  return (
    <li>
      <button type="button" className="rk-row" onClick={() => onSelect(f.id)}>
        <span className="rk-rank">{rank}</span>
        <span className="rk-main">
          <span className="rk-who" lang="en">
            {who}
          </span>
          {what && <span className="rk-what">{what}</span>}
          {meta && <span className="rk-meta">{meta}</span>}
        </span>
        <span className="rk-side">
          <span className="rk-amount">{money(f.monetary_exposure_inr, t)}</span>
          <span className="rk-open">
            {t.evidence}
            <ArrowUpRight size={14} aria-hidden="true" />
          </span>
        </span>
      </button>
    </li>
  );
}

function LangSwitch({ lang, onChange }: { lang: Lang; onChange: (l: Lang) => void }) {
  return (
    <div className="rk-langs" role="group" aria-label="Language">
      {LANGS.map((l) => (
        <button
          key={l}
          type="button"
          lang={l}
          className={`rk-lang ${l === lang ? 'is-active' : ''}`}
          aria-pressed={l === lang}
          onClick={() => onChange(l)}
        >
          {STRINGS[l].name}
        </button>
      ))}
    </div>
  );
}

export function TopDiscrepancies({ onSelectFinding }: { onSelectFinding: (id: string | number) => void }) {
  const [lang, setLang] = useState<Lang>(() => readLang());
  const [items, setItems] = useState<FindingItem[]>([]);
  const t = STRINGS[lang];

  useEffect(() => {
    let cancelled = false;
    api
      .getFindings({ status: 'confirmed', sort: 'amount', limit: 5 })
      .then((res) => {
        if (!cancelled) setItems(res.items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (items.length === 0) return null;

  return (
    <section className="rk-home" lang={lang} aria-labelledby="rk-home-title">
      <div className="rk-home-head">
        <div>
          <div className="rk-eyebrow">{t.eyebrow}</div>
          <h2 id="rk-home-title" className="rk-home-title">
            {t.title}
          </h2>
        </div>
        <LangSwitch
          lang={lang}
          onChange={(l) => {
            setLang(l);
            saveLang(l);
          }}
        />
      </div>
      <ol className="rk-list">
        {items.map((f, i) => (
          <RankRow key={f.id} f={f} rank={i + 1} t={t} onSelect={onSelectFinding} />
        ))}
      </ol>
      <div className="rk-home-foot">
        <span>{t.notAccusation}</span>
        <Link to={`/top?lang=${lang}`} className="btn btn-primary">
          {t.seeAll}
        </Link>
      </div>
    </section>
  );
}

interface RankingPageProps {
  onSelectFinding: (id: string | number) => void;
  refreshTick: number;
}

export function RankingPage({ onSelectFinding, refreshTick }: RankingPageProps) {
  const [lang, setLang] = useState<Lang>(() => readLang());
  const [sort, setSort] = useState<'amount' | 'recent'>('amount');
  const [state, setState] = useState('');
  const [states, setStates] = useState<string[]>([]);
  const [items, setItems] = useState<FindingItem[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const t = STRINGS[lang];

  const changeLang = (l: Lang) => {
    setLang(l);
    saveLang(l);
    const url = new URL(window.location.href);
    url.searchParams.set('lang', l);
    window.history.replaceState({}, '', url.pathname + url.search + url.hash);
  };

  useEffect(() => {
    let cancelled = false;
    api
      .getFindingPatterns()
      .then((res) => {
        if (!cancelled && res.by_jurisdiction) setStates(Object.keys(res.by_jurisdiction));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  const selection = useRef(0);

  const load = (offset: number, ticket: number) => {
    setLoading(true);
    setError(false);
    return api
      .getFindings({
        status: 'confirmed',
        sort,
        jurisdiction: state || undefined,
        limit: PAGE,
        offset,
      })
      .then((res) => {
        if (ticket !== selection.current) return;
        setItems((prev) => (offset === 0 ? res.items : [...prev, ...res.items]));
        setTotal(res.total);
        setHasMore(res.has_more);
      })
      .catch(() => {
        if (ticket === selection.current) setError(true);
      })
      .finally(() => {
        if (ticket === selection.current) setLoading(false);
      });
  };

  useEffect(() => {
    selection.current += 1;
    void load(0, selection.current);
  }, [sort, state, refreshTick]);

  return (
    <div className="rk" lang={lang}>
      <header className="rk-head">
        <LangSwitch lang={lang} onChange={changeLang} />
        <div className="rk-eyebrow">{t.eyebrow}</div>
        <h1 className="rk-title">{t.title}</h1>
        <p className="rk-lede">{t.lede}</p>
        <p className="rk-caution">{t.notAccusation}</p>
      </header>

      <div className="rk-controls">
        <div className="rk-sort" role="group" aria-label={t.sortAmount}>
          <button
            type="button"
            className={`rk-sort-btn ${sort === 'amount' ? 'is-active' : ''}`}
            aria-pressed={sort === 'amount'}
            onClick={() => setSort('amount')}
          >
            {t.sortAmount}
          </button>
          <button
            type="button"
            className={`rk-sort-btn ${sort === 'recent' ? 'is-active' : ''}`}
            aria-pressed={sort === 'recent'}
            onClick={() => setSort('recent')}
          >
            {t.sortRecent}
          </button>
        </div>
        {states.length > 1 && (
          <label className="rk-state">
            <span>{t.state}</span>
            <select id="rk-state" value={state} onChange={(e) => setState(e.target.value)}>
              <option value="">{t.allIndia}</option>
              {states.map((s) => (
                <option key={s} value={s} lang="en">
                  {s}
                </option>
              ))}
            </select>
          </label>
        )}
        <span className="rk-count">{loading && items.length === 0 ? t.loading : fill(t.count, { n: total.toLocaleString(t.locale) })}</span>
      </div>

      {error && items.length === 0 && <div className="rk-empty">{t.empty}</div>}
      {!loading && !error && items.length === 0 && <div className="rk-empty">{t.empty}</div>}

      {items.length > 0 && (
        <ol className="rk-list">
          {items.map((f, i) => (
            <RankRow key={f.id} f={f} rank={i + 1} t={t} onSelect={onSelectFinding} />
          ))}
        </ol>
      )}

      {hasMore && (
        <div className="rk-more">
          <button type="button" className="btn btn-secondary" disabled={loading} onClick={() => void load(items.length, selection.current)}>
            {loading ? t.loading : t.loadMore}
          </button>
        </div>
      )}

      <p className="rk-note">{t.namesNote}</p>
    </div>
  );
}
