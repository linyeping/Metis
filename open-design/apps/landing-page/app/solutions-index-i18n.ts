/*
 * Copy for the `/solutions/` index (overview) page — the hub that links to
 * every Solution sub-page (Use cases + Roles). Only the page's own chrome
 * lives here (label / heading / lead); each card's text is pulled from that
 * sub-page's own `getSolutionPageCopy` breadcrumb + description, and the two
 * group headings reuse the header nav labels (`nav.useCases` / `nav.roles`),
 * so there is a single source of truth per string.
 *
 * Keyed by every `LandingLocaleCode`; the `Record` type makes a missing
 * locale a typecheck error, matching the 18-locale guarantee the rest of the
 * landing i18n relies on.
 */
import type { LandingLocaleCode } from './i18n';

export interface SolutionsIndexCopy {
  /** Small kicker label above the H1. */
  label: string;
  /** Page H1. */
  heading: string;
  /** One-sentence intro under the H1. */
  lead: string;
}

const COPY: Record<LandingLocaleCode, SolutionsIndexCopy> = {
  en: {
    label: 'SOLUTION',
    heading: 'Metis Design solutions',
    lead: "Find the right way to use Metis Design — by what you're making, and by the role you play.",
  },
  zh: {
    label: '解决方案',
    heading: 'Metis Design 解决方案',
    lead: '找到最适合你的 Metis Design 使用方式——既可按你要构建的内容（使用场景）查找，也可按你的角色查找。',
  },
  'zh-tw': {
    label: '解決方案',
    heading: 'Metis Design 解決方案',
    lead: '依你要打造的內容、依你扮演的角色，找到最適合運用 Metis Design 的方式。',
  },
  ja: {
    label: 'ソリューション',
    heading: 'Metis Design のソリューション',
    lead: '作りたいもの（ユースケース）と、あなたの役割の両方から、Metis Design を活用する最適な方法を見つけましょう。',
  },
  ko: {
    label: '솔루션',
    heading: 'Metis Design 솔루션',
    lead: '만들려는 것과 맡은 역할에 따라 정리된, Metis Design를 활용하는 가장 알맞은 방법을 찾아보세요.',
  },
  de: {
    label: 'Lösung',
    heading: 'Metis Design Lösungen',
    lead: 'Finden Sie den passenden Weg, Metis Design zu nutzen – sortiert danach, was Sie entwickeln, und nach Ihrer Rolle.',
  },
  fr: {
    label: 'SOLUTION',
    heading: 'Solutions Metis Design',
    lead: "Trouvez la meilleure façon d'utiliser Metis Design — selon ce que vous créez et selon votre rôle.",
  },
  ru: {
    label: 'Решение',
    heading: 'Решения Metis Design',
    lead: 'Найдите подходящий способ использовать Metis Design — по тому, что вы создаёте, и по вашей роли.',
  },
  es: {
    label: 'SOLUCIÓN',
    heading: 'Soluciones de Metis Design',
    lead: 'Encuentra la mejor manera de usar Metis Design: según lo que estás creando y según tu rol.',
  },
  'pt-br': {
    label: 'Solução',
    heading: 'Soluções do Metis Design',
    lead: 'Encontre a maneira certa de usar o Metis Design — pelo que você está criando e pela função que você desempenha.',
  },
  it: {
    label: 'Soluzione',
    heading: 'Le soluzioni di Metis Design',
    lead: 'Trova il modo giusto di usare Metis Design, organizzato in base a ciò che stai creando e al ruolo che ricopri.',
  },
  vi: {
    label: 'Giải pháp',
    heading: 'Giải pháp Metis Design',
    lead: 'Tìm cách phù hợp để sử dụng Metis Design — theo những gì bạn đang xây dựng, và theo vai trò của bạn.',
  },
  pl: {
    label: 'Rozwiązanie',
    heading: 'Rozwiązania Metis Design',
    lead: 'Znajdź właściwy sposób korzystania z Metis Design — według tego, co tworzysz, i według roli, jaką pełnisz.',
  },
  id: {
    label: 'Solusi',
    heading: 'Solusi Metis Design',
    lead: 'Temukan cara yang tepat untuk menggunakan Metis Design — berdasarkan apa yang Anda buat, dan berdasarkan peran yang Anda jalankan.',
  },
  nl: {
    label: 'OPLOSSING',
    heading: 'Metis Design-oplossingen',
    lead: 'Vind de juiste manier om Metis Design te gebruiken — op basis van wat je maakt en van de rol die je vervult.',
  },
  ar: {
    label: 'حل',
    heading: 'حلول Metis Design',
    lead: 'اعثر على الطريقة المناسبة لاستخدام Metis Design — حسب ما تبنيه، وحسب الدور الذي تؤديه.',
  },
  tr: {
    label: 'Çözüm',
    heading: 'Metis Design çözümleri',
    lead: "Metis Design'ı kullanmanın doğru yolunu bulun — ne ürettiğinize ve hangi rolü üstlendiğinize göre.",
  },
  uk: {
    label: 'Рішення',
    heading: 'Рішення Metis Design',
    lead: 'Знайдіть свій спосіб використання Metis Design — за тим, що ви створюєте, і за вашою роллю.',
  },
};

export function getSolutionsIndexCopy(locale: LandingLocaleCode): SolutionsIndexCopy {
  return COPY[locale] ?? COPY.en;
}
