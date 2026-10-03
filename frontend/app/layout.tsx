import './globals.css';

export const metadata = {
  title: 'SnapMind — Find what you saved',
  description: 'A searchable visual memory for your screenshots.',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
