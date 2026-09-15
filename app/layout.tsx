import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FLY FUND — The Trading Room",
  description: "果蝇基金经理的交易室。查看市场行情、探索书架并体验演示投喂。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" >
      <body >{children}</body>
    </html>
  );
}
