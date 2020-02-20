import base64
import io
import lzma
import struct
import unittest
from datetime import datetime, time, timedelta, timezone

from zoneinfo import IANAZone


class IANAZoneTest(unittest.TestCase):
    def _load_local_file(self, key):
        f = load_zoneinfo_file(key)
        return IANAZone.from_file(f, key=key)

    def test_dublin_offsets(self):
        tzi = self._load_local_file("Europe/Dublin")

        DMT = ("DMT", timedelta(seconds=-1521), timedelta(hours=0))
        IST_0 = ("IST", timedelta(seconds=2079), timedelta(hours=1))
        GMT_0 = ("GMT", timedelta(0), timedelta(0))
        BST = ("BST", timedelta(hours=1), timedelta(hours=1))
        GMT_1 = ("GMT", timedelta(0), timedelta(hours=-1))
        IST_1 = ("IST", timedelta(hours=1), timedelta(0))

        test_cases = [
            # Unambiguous
            (datetime(1800, 1, 1, tzinfo=tzi), DMT),
            (datetime(1916, 4, 1, tzinfo=tzi), DMT),
            (datetime(1916, 5, 21, 1, tzinfo=tzi), DMT),
            (datetime(1916, 5, 21, 4, tzinfo=tzi), IST_0),
            (datetime(1916, 10, 2, 0, tzinfo=tzi), GMT_0),
            (datetime(1917, 4, 7, 0, tzinfo=tzi), GMT_0),
            (datetime(1917, 4, 9, 0, tzinfo=tzi), BST),
            (datetime(2023, 2, 14, 0, tzinfo=tzi), GMT_1),
            (datetime(2023, 6, 18, 0, tzinfo=tzi), IST_1),
            (datetime(2023, 11, 17, 0, tzinfo=tzi), GMT_1),
            # After 2038: Requires version 2 file
            (datetime(2487, 3, 1, 0, tzinfo=tzi), GMT_1),
            (datetime(2487, 6, 1, 0, tzinfo=tzi), IST_1),
            # Gaps
            (datetime(1916, 5, 21, 2, 25, 21, fold=0, tzinfo=tzi), DMT),
            (datetime(1916, 5, 21, 2, 25, 21, fold=1, tzinfo=tzi), IST_0),
            (datetime(1917, 4, 8, 1, 30, tzinfo=tzi), GMT_0),
            (datetime(1917, 4, 8, 2, 30, fold=0, tzinfo=tzi), GMT_0),
            (datetime(1917, 4, 8, 2, 30, fold=1, tzinfo=tzi), BST),
            (datetime(2024, 3, 31, 1, 30, fold=0, tzinfo=tzi), GMT_1),
            (datetime(2024, 3, 31, 1, 30, fold=1, tzinfo=tzi), IST_1),
            (datetime(2823, 3, 26, 1, 30, fold=0, tzinfo=tzi), GMT_1),
            (datetime(2823, 3, 26, 1, 30, fold=1, tzinfo=tzi), IST_1),
            # Folds
            (datetime(2024, 10, 27, 1, 30, fold=0, tzinfo=tzi), IST_1),
            (datetime(2024, 10, 27, 1, 30, fold=1, tzinfo=tzi), GMT_1),
            (datetime(2823, 10, 29, 1, 30, fold=0, tzinfo=tzi), IST_1),
            (datetime(2823, 10, 29, 1, 30, fold=1, tzinfo=tzi), GMT_1),
        ]

        for dt, (tzname, utcoff, dst) in test_cases:
            with self.subTest(dt=dt, tzname=tzname):
                self.assertEqual(dt.tzname(), tzname)
                self.assertEqual(dt.utcoffset(), utcoff)
                self.assertEqual(dt.dst(), dst)


class TZStrTest(unittest.TestCase):
    def _zone_from_tzstr(self, tzstr):
        """Creates a zoneinfo file following a POSIX rule."""
        zonefile = io.BytesIO()
        # Version 1 header
        zonefile.write(b"TZif")  # Magic value
        zonefile.write(b"3")  # Version
        zonefile.write(b" " * 15)  # Reserved
        # We will not write any of the manual transition parts
        zonefile.write(struct.pack(">6l", 0, 0, 0, 0, 0, 0))

        # Version 2+ header
        zonefile.write(b"TZif")  # Magic value
        zonefile.write(b"3")  # Version
        zonefile.write(b" " * 15)  # Reserved
        zonefile.write(struct.pack(">6l", 0, 0, 0, 1, 1, 4))

        # Add an arbitrary offset to make things easier
        zonefile.write(struct.pack(">1q", -(2 ** 32)))
        zonefile.write(struct.pack(">1B", 0))
        zonefile.write(struct.pack(">lbb", -17760, 0, 0))
        zonefile.write(b"LMT\x00")

        # Write the footer
        zonefile.write(b"\x0A")
        zonefile.write(tzstr.encode("ascii"))
        zonefile.write(b"\x0A")

        zonefile.seek(0)

        return IANAZone.from_file(zonefile, key=tzstr)

    def test_m_spec_fromutc(self):
        UTC = timezone.utc
        test_cases = [
            (
                "EST5EDT,M3.2.0/4:00,M11.1.0/3:00",
                [
                    # fmt: off
                    (datetime(2019, 3, 9, 17), datetime(2019, 3, 9, 12)),
                    (datetime(2019, 3, 10, 8, 59), datetime(2019, 3, 10, 3, 59)),
                    (datetime(2019, 3, 10, 9, 0), datetime(2019, 3, 10, 5)),
                    (datetime(2019, 11, 2, 16, 0), datetime(2019, 11, 2, 12)),
                    (datetime(2019, 11, 3, 5, 59), datetime(2019, 11, 3, 1, 59)),
                    (datetime(2019, 11, 3, 6, 0), datetime(2019, 11, 3, 2)),
                    (datetime(2019, 11, 3, 7, 0), datetime(2019, 11, 3, 2, fold=1)),
                    (datetime(2019, 11, 3, 8, 0), datetime(2019, 11, 3, 3)),
                    # fmt: on
                ],
            ),
            # TODO: England, Australia, Dublin
        ]

        for tzstr, test_values in test_cases:
            tzi = self._zone_from_tzstr(tzstr)
            for dt_utc_naive, dt_local_naive in test_values:
                # Test conversion UTC -> TZ
                with self.subTest(
                    tzstr=tzstr, utc=dt_utc_naive, exp=dt_local_naive
                ):
                    dt_utc = dt_utc_naive.replace(tzinfo=UTC)
                    dt_actual = dt_utc.astimezone(tzi)
                    dt_actual_naive = dt_actual.replace(tzinfo=None)

                    self.assertEqual(dt_actual_naive, dt_local_naive)
                    self.assertEqual(dt_actual.fold, dt_local_naive.fold)

                # Test conversion TZ -> UTC
                with self.subTest(
                    tzstr=tzstr, local=dt_local_naive, utc=dt_utc_naive
                ):
                    dt_local = dt_local_naive.replace(tzinfo=tzi)
                    utc_expected = dt_utc_naive.replace(tzinfo=UTC)
                    utc_actual = dt_local.astimezone(UTC)

                    self.assertEqual(utc_actual, utc_expected)

    def test_m_spec_localized(self):
        """Tests that the Mm.n.d specification works"""
        # Test cases are a list of entries, where each entry is:
        # (tzstr, [(datetime, tzname, offset), ...])

        # TODO: Replace with tzstr + transitions?
        test_cases = [
            # Transition to EDT on the 2nd Sunday in March at 4 AM, and
            # transition back on the first Sunday in November at 3AM
            (
                "EST5EDT,M3.2.0/4:00,M11.1.0/3:00",
                [
                    # fmt: off
                    (datetime(2019, 3, 9), "EST", timedelta(hours=-5)),
                    (datetime(2019, 3, 10, 3, 59), "EST", timedelta(hours=-5)),
                    (datetime(2019, 3, 10, 4, 0, fold=0), "EST", timedelta(hours=-5)),
                    (datetime(2019, 3, 10, 4, 0, fold=1), "EDT", timedelta(hours=-4)),
                    (datetime(2019, 3, 10, 4, 1, fold=0), "EST", timedelta(hours=-5)),
                    (datetime(2019, 3, 10, 4, 1, fold=1), "EDT", timedelta(hours=-4)),
                    (datetime(2019, 11, 2), "EDT", timedelta(hours=-4)),
                    (datetime(2019, 11, 3, 1, 59, fold=1), "EDT", timedelta(hours=-4)),
                    (datetime(2019, 11, 3, 2, 0, fold=0), "EDT", timedelta(hours=-4)),
                    (datetime(2019, 11, 3, 2, 0, fold=1), "EST", timedelta(hours=-5)),
                    (datetime(2020, 3, 8, 3, 59), "EST", timedelta(hours=-5)),
                    (datetime(2020, 3, 8, 4, 0, fold=0), "EST", timedelta(hours=-5)),
                    (datetime(2020, 3, 8, 4, 0, fold=1), "EDT", timedelta(hours=-4)),
                    (datetime(2020, 11, 1, 1, 59, fold=1), "EDT", timedelta(hours=-4)),
                    (datetime(2020, 11, 1, 2, 0, fold=0), "EDT", timedelta(hours=-4)),
                    (datetime(2020, 11, 1, 2, 0, fold=1), "EST", timedelta(hours=-5)),
                    # fmt: on
                ],
            ),
            # Transition to BST happens on the last Sunday in March at 1 AM GMT
            # and the transition back happens the last Sunday in October at 2AM BST
            (
                "GMT0BST-1,M3.5.0/1:00,M10.5.0/2:00",
                [
                    # fmt: off
                    (datetime(2019, 3, 30), "GMT", timedelta(hours=0)),
                    (datetime(2019, 3, 31, 0, 59), "GMT", timedelta(hours=0)),
                    (datetime(2019, 3, 31, 2, 0), "BST", timedelta(hours=1)),
                    (datetime(2019, 10, 26), "BST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 0, 59, fold=1), "BST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 1, 0, fold=0), "BST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 2, 0, fold=1), "GMT", timedelta(hours=0)),
                    (datetime(2020, 3, 29, 0, 59), "GMT", timedelta(hours=0)),
                    (datetime(2020, 3, 29, 2, 0), "BST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 0, 59, fold=1), "BST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 1, 0, fold=0), "BST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 2, 0, fold=1), "GMT", timedelta(hours=0)),
                    # fmt: on
                ],
            ),
            # Austrialian time zone - DST start is chronologically first
            (
                "AEST-10AEDT,M10.1.0/2,M4.1.0/3",
                [
                    # fmt: off
                    (datetime(2019, 4, 6), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 4, 7, 1, 59), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 4, 7, 1, 59, fold=1), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 4, 7, 2, 0, fold=0), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 4, 7, 2, 1, fold=0), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 4, 7, 2, 0, fold=1), "AEST", timedelta(hours=10)),
                    (datetime(2019, 4, 7, 2, 1, fold=1), "AEST", timedelta(hours=10)),
                    (datetime(2019, 4, 7, 3, 0, fold=0), "AEST", timedelta(hours=10)),
                    (datetime(2019, 4, 7, 3, 0, fold=1), "AEST", timedelta(hours=10)),
                    (datetime(2019, 10, 5, 0), "AEST", timedelta(hours=10)),
                    (datetime(2019, 10, 6, 1, 59), "AEST", timedelta(hours=10)),
                    (datetime(2019, 10, 6, 2, 0, fold=0), "AEST", timedelta(hours=10)),
                    (datetime(2019, 10, 6, 2, 0, fold=1), "AEDT", timedelta(hours=11)),
                    (datetime(2019, 10, 6, 3, 0), "AEDT", timedelta(hours=11)),
                    # fmt: on
                ],
            ),
            (
                "IST-1GMT0,M10.5.0,M3.5.0/1",
                [
                    # fmt: off
                    (datetime(2019, 3, 30), "GMT", timedelta(hours=0)),
                    (datetime(2019, 3, 31, 0, 59), "GMT", timedelta(hours=0)),
                    (datetime(2019, 3, 31, 2, 0), "IST", timedelta(hours=1)),
                    (datetime(2019, 10, 26), "IST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 0, 59, fold=1), "IST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 1, 0, fold=0), "IST", timedelta(hours=1)),
                    (datetime(2019, 10, 27, 2, 0, fold=1), "GMT", timedelta(hours=0)),
                    (datetime(2020, 3, 29, 0, 59), "GMT", timedelta(hours=0)),
                    (datetime(2020, 3, 29, 2, 0), "IST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 0, 59, fold=1), "IST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 1, 0, fold=0), "IST", timedelta(hours=1)),
                    (datetime(2020, 10, 25, 2, 0, fold=1), "GMT", timedelta(hours=0)),
                    # fmt: on
                ],
            ),
        ]

        for tzstr, test_values in test_cases:
            tzi = self._zone_from_tzstr(tzstr)
            self.assertEqual(str(tzi), tzstr)

            for dt_naive, expected_tzname, expected_tzoffset in test_values:
                dt = dt_naive.replace(tzinfo=tzi)
                with self.subTest(tzstr=tzstr, dt=dt):
                    self.assertEqual(dt.tzname(), expected_tzname)
                    self.assertEqual(dt.utcoffset(), expected_tzoffset)


###
#
# Some example zoneinfo files: These are lzma-compressed and then b85 encoded
# encoded, to minimize the space taken up in this file
def load_zoneinfo_file(key):
    if key not in ZONEINFO_FILES:
        raise ValueError(f"Zoneinfo file not found: {key}")

    raw = ZONEINFO_FILES[key]
    raw = b"".join(map(bytes.strip, raw.split(b"\n")))
    decoded = base64.b85decode(raw)
    decompressed = lzma.decompress(decoded)

    return io.BytesIO(decompressed)


AMERICA_LOS_ANGELES = b"""
{Wp48S^xk9=GL@E0stWa8~^|S5YJf5;0qH3OkDsf7KxBg5R*;z{h&-RlhRYu$%jt%!jv+IJxhE=%W1
?wYb!37Rb?(rgwFIAQI{L#8r*zy!$TMtER_1(vn(Zix^{AVB1(jwr$iL6h0Z!28Gb~UW@0~e512{Z%
8}Qzdnjl~wJ1{c2>`Z@1A~t&lyL{p{eM{5)QGf7Mo5FW9==mlyXJt2UwpntR7H0eSq!(aYq#aqUz&R
M*tvuMI)AsM?K3-dV3-TT{t)!Iy#JTo=tXkzAM9~j2YbiOls3(H8Dc>Y|D1aqL51vjLbpYG;GvGTQB
4bXuJ%mA;(B4eUpu$$@zv2vVcq-Y)VKbzp^teiuzy}R{Luv<C;_cPe*n$Z<jeC9ogWF9=1mvvUYXS>
DjpuVb`79O+CBmg{Wx!bvx$eu4zRE&PehMb=&G<9$>iZ|bFE)0=4I?KLFGBC0I(0_svgw0%FiMsT%k
oo*!nEYc6GY@QnU}&4Isg;l=|khi(!VaiSE2=Ny`&&tpi~~;{$u<GHlsr3Ze!iYsU205RFKsLnrXwO
L?Mq08xffgS{6hE|figx+&N%wbO}re@|}$l;g_6J-Wl%j|qev8A<T?NJ)`;2neGi_DHE4ET*W!c*gg
PAgU+LE9=bH7;maCUikw^R)UM;TdVvNkQ;FGgN=yQER`SZ1nOgPXr0LCebLety&}kVdmVmB=8eSgtd
!1%p=a2wooIL!Da}OPXvKBfRo?YxqS>N}%f|7mBhAy;<Er2&_LfND#qXN~Mkgf!@4VFAHr%$c)wrKA
2cJYWK2>s3YT^sy!$eG~?`9mNJC9@4Bac_p^BZh)Yd_rWW5qh-?tKY(>5VHOL*iT8P@wCavLj^yYbn
DR+4ukhS+xPrpl)iqB?u)bj9a2aW==g6G3lCJd>(+Blf<d4CF%7utlBUDki}J-!_Dy}5S(MrxSXy~$
Z+hgH3P^<<w7D72L7I-R%H3(xm&q_DXxkp$owLTS6Wzkhc3nn;laROa3)6hl&gH#)2Lif8fZe$@Cde
J-Zn&*>r)~^40F4f>cRZ^UF;RibfZ>0m73hRC{$vTfC(STN`g7(B<=Z2556{}0`?p&|Akkst!4Xy4O
T;A@c$XTUI3FRRjy*KA7uC56FD)z^X{WV*sr(w!c$W357o!&eLO2wTDNOyw@gf(&R<<LF_3URI4=Ei
`-%dM3T66j#9!aG7&b_@g1-9vo?DzXZ5vGaf~w__p_@_X?OdvQ_r5bvy2hpESTf+{p?jL+!~!{g8-<
-5$@d8EZV&-5@a|;^1gB*R-~{EHFA-td_G2bt;~Y}>t;=-Tu1TV{>%8ZVATC9tjD8|(&`$9YHvZ9bV
e#>w|8c;Tg|xE&)`*}LwM*E}q}q8^Qja%p`_U)*5DdLI9O@!e=3jFjOCrCq28b_bb;s>%D#iJBCWJi
{JH!Js;6nfayos$kq^OEX00HO-lokL0!mqm{vBYQl0ssI200dcD
"""

EUROPE_DUBLIN = b"""
{Wp48S^xk9=GL@E0stWa8~^|S5YJf5;0>b$_+0=h7KxBg5R*;&J77#T_U2R5sleVWFDmK~Kzj5oh@`
<njquRZ&tJIS(cXp1>QKHvW^6V{jU-w>qg1tSt0c^vh;?qAqA0%t?;#S~6U8Qiv&f1s9IH#g$m1k1a
#3+lylw4mwT4QnEUUQdwg+xnEcBlgu31bAVabn41OMZVLGz6NDwG%XuQar!b>GI{qSahE`AG}$kRWb
uI~JCt;38)Xwbb~Qggs55t+MAHIxgDxzTJ;2xXx99+qCy445kC#v_l8fx|G&jlVvaciR<-wwf22l%4
(t@S6tnX39#_K(4S0fu$FUs$isu<UOJYm|4)2iaEpsajn@}B#rnY=Cg_TXsm-A)*adXV&$klNTn3n{
XXlaquu}6m{k%oRmY0Yyhlj*<W{D5m22}OiqnwHT!tnK`wPqx?wiF%v{ipTrOkcJ5P@7OC4(-l`*&S
B$Wd4Vf8gn?>d<i@%mP*e*ttDj`9M1;9$YV@dhT)DVcwdq(Ly~KDm_&KL?{_mFwwYtJqRZBk)i1FVQ
y!40w_KyAg?hIA=_{(3#S0eWsF8f%_4Zza$4@$lSmov+Huyn$vP^zJ|8-<C3#q#0kEs9cNg^xUR(m?
wEWt-DGctAh2nIo~fz%$m$I41=b_WuJ6M9g#A9_Epwqw{d0B|vzmg#_y<=_>9IKzCXB<o`d)**5V6g
!<<Jw1n5TrN-$)aYz4cLsTmpsUf-6L7ix+kk>78NkARYq@9Dc0TGkhz);NtM_SSzEffNl{2^*CKGdp
52h!52A)6q9fUSltXF{T*Ehc9Q7u8!W7pE(Fv$D$cKUAt6wY=DA1mGgxC*VXq_If3G#FY6-Voj`fIK
k`0}Cc72_SD{v>468LV{pyBI33^p0E?}RwDA6Pkq--C~0jF&Z@Pv!dx_1SN_)jwz@P$(oK%P!Tk9?f
RjK88yxhxlcFtTjjZ$DYssSsa#ufYrR+}}nKS+r384o~!Uw$nwTbF~qgRsgr0N#d@KIinx%<pnyQ!|
>hQB(SJyjJtDtIy(%mDm}ZBGN}dV6K~om|=UVGkbciQ=^$_14|gT21!YQ)@y*Rd0i_lS6gtPBE9+ah
%WIJPwzUTjIr+J1XckkmA!6WE16%CVAl{Dn&-)=G$Bjh?bh0$Xt1UDcgXJjXzzojuw0>paV~?Sa`VN
3FysqF<S*L0RYSAY3jt(8wCD04RfyEcP(RNT%x7k(7m-9H3{zuQ`RZy-Rz%*&dldDVFF+TwSAPO1wR
X^5W5@xJ9{vWw?rc^NH({%Ie<rxKqSVy!Le-_`U&@W_(D+>xTzfKVAu*ucq#+m=|KSSMvp_#@-lwd+
q*ueFQ^5<D+|jLr?k{O39i8AX2Qb^zi9A<7XD1y!-W2|0Hk8JVkN;gl><|<0R-u4qYMbRqzSn&Q7jS
uvc%b+EZc%>nI(+&0Tl1Y>a6v4`uNFD-7$QrhHgS7Wnv~rDgfH;rQw3+m`LJxoM4v#gK@?|B{RHJ*V
xZgk#!p<_&-sjxOda0YaiJ1UnG41VPv(Et%ElzKRMcO$AfgU+Xnwg5p2_+NrnZ1WfEj^fmHd^sx@%J
WKkh#zaK0ox%rdP)zUmGZZnqmZ_9L=%6R8ibJH0bOT$AGhDo6{fJ?;_U;D|^>5by2ul@i4Zf()InfF
N}00EQ=q#FPL>RM>svBYQl0ssI200dcD
"""

ZONEINFO_FILES = {
    "America/Los_Angeles": AMERICA_LOS_ANGELES,
    "Europe/Dublin": EUROPE_DUBLIN,
}
