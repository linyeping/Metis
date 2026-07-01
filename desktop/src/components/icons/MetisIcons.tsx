/**
 * Metis 自定义欢迎页图标
 * 接口与 lucide-react 兼容（size / strokeWidth / className / aria-hidden）
 */

interface IconProps {
  size?: number;
  strokeWidth?: number;
  className?: string;
  'aria-hidden'?: boolean | 'true' | 'false';
}

/**
 * Chat 欢迎图标 — 微信风格双气泡
 * 大气泡（左上）+ 小气泡（右下）错落叠放，带尾巴
 */
export function ChatBubbleIcon({
  size = 24,
  strokeWidth = 2,
  className,
  'aria-hidden': ariaHidden,
}: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={ariaHidden}
    >
      {/* 大气泡：左上，宽，带左下角尾巴 */}
      <path d="M16 3H5a3 3 0 00-3 3v5a3 3 0 003 3h2l-1.5 3.5 4-3.5H16a3 3 0 003-3V6a3 3 0 00-3-3z" />
      {/* 小气泡：右下，较小，与大气泡右侧区域叠放，带右下角尾巴 */}
      <path d="M20 11h-7a2 2 0 00-2 2v3a2 2 0 002 2h3.5l1 2.5-.5-2.5H20a2 2 0 002-2v-3a2 2 0 00-2-2z" />
    </svg>
  );
}

/**
 * Cowork 欢迎图标 — 2×2 四格不同小图标
 * 左上：代码标签 </>   右上：搜索放大镜
 * 左下：文档横线       右下：完成对勾 ✓
 */
export function CoworkTaskIcon({
  size = 24,
  strokeWidth = 2,
  className,
  'aria-hidden': ariaHidden,
}: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={ariaHidden}
    >
      {/* 左上格：代码 </> */}
      <path d="M4 4.75L2.5 5.75 4 6.75" />
      <line x1="6.2" y1="4" x2="4.8" y2="7.5" />
      <path d="M7.5 4.75L9 5.75 7.5 6.75" />

      {/* 右上格：搜索放大镜 */}
      <circle cx="18" cy="5.5" r="2.2" />
      <line x1="19.6" y1="7.1" x2="21.2" y2="8.7" />

      {/* 左下格：文档三行 */}
      <line x1="3" y1="16.5" x2="8.5" y2="16.5" />
      <line x1="3" y1="18.5" x2="8.5" y2="18.5" />
      <line x1="3" y1="20.5" x2="6.5" y2="20.5" />

      {/* 右下格：完成对勾 ✓ */}
      <path d="M15.5 18.5l2 2 4-4" />
    </svg>
  );
}
