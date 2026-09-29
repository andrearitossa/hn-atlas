import sqlite3
import unittest
from unittest.mock import Mock, patch

import newsletter
from scripts.weekly_newsletters import edition_at
from datetime import datetime


def post(i, score=20, comments=10, **extra):
    return dict(id=i, title=f'Story {i}', url=f'https://example.org/{i}',
                score=score, descendants=comments, text='', **extra)


class NewsletterTests(unittest.TestCase):
    def test_engagement_shortlist_has_no_minimum_and_deduplicates(self):
        rows=[post(1,score=1,comments=0),post(2,score=1,comments=0)]
        rows[1]['url']=rows[0]['url']+'?utm_source=hn'
        self.assertEqual(len(newsletter.shortlist(rows)),1)
        self.assertEqual(len(newsletter.shortlist([post(i) for i in range(30)])),15)
        self.assertEqual(newsletter.shortlist([post(1,comments=0),post(2,comments=100)])[0]['id'],2)

    def test_week_windows_are_adjacent_at_both_dst_changes(self):
        conn=sqlite3.connect(':memory:');self.addCleanup(conn.close);conn.row_factory=sqlite3.Row
        conn.executescript('CREATE TABLE stories(id,title,url,text,time,score,descendants,dead,deleted); CREATE TABLE story_topics(id,topic);')
        for date,hours in [('2026-03-29T18:00:00+02:00',167),('2026-10-25T18:00:00+01:00',169)]:
            end=edition_at(int(datetime.fromisoformat(date).timestamp()));start=edition_at(end-1)
            self.assertEqual(end-start,hours*3600)
            conn.execute('DELETE FROM stories');conn.execute('DELETE FROM story_topics')
            for i,t in enumerate([start,start+1,end,end+1]):
                conn.execute("INSERT INTO stories VALUES(?,'x','','',?,1,0,0,0)",(i,t))
                conn.execute('INSERT INTO story_topics VALUES(?,1)',(i,))
            self.assertEqual([p['id'] for p in newsletter.candidates(conn,1,start,end)],[1,2])

    def test_selection_uses_overview_and_respects_llm_order(self):
        with patch.object(newsletter,'overview',side_effect=lambda p:dict(p,summary='Overview',text='Body')), \
             patch.object(newsletter.llm,'ask_json',return_value={'ids':[1,3]}) as ask:
            result=newsletter.select([post(i) for i in range(20)],'Robotics')
            # 1 and 3 are outside this tied-score shortlist, so invalid output falls back.
            self.assertEqual(len(result),5)
            ask.return_value={'ids':list(range(19,4,-1))}
            result=newsletter.select([post(i) for i in range(20)],'Robotics')
            self.assertEqual([p['id'] for p in result],[19,18,17,16,15])
            self.assertIn('Overview',ask.call_args.args[0]);self.assertIn('Body',ask.call_args.args[0])

    def test_failed_or_invalid_selection_logs_and_uses_score_order(self):
        rows=[post(i,score=i+1) for i in range(8)]
        with patch.object(newsletter,'overview',side_effect=lambda p:p):
            for response in [{'ids':[7,7]},{'ids':[999]},{'ids':[]},{'ids':[True]},{}]:
                with patch.object(newsletter.llm,'ask_json',return_value=response),self.assertLogs('newsletter',level='ERROR'):
                    self.assertEqual([p['id'] for p in newsletter.select(rows,'Topic')],[7,6,5,4,3])
            with patch.object(newsletter.llm,'ask_json',side_effect=TimeoutError),self.assertLogs('newsletter',level='ERROR'):
                self.assertEqual(len(newsletter.select(rows,'Topic')),5)

    def test_fetch_failure_uses_body_and_summary_is_shared(self):
        with patch.object(newsletter,'page_text',side_effect=ValueError('unavailable')), \
             patch.object(newsletter.llm,'ask_json',return_value={'overview':'Useful <overview>.'}) as ask, \
             self.assertLogs('newsletter',level='ERROR'):
            row=post(1);row['text']='<p>Real post body</p>'
            result=newsletter.overview(row)
            self.assertIn('Real post body',ask.call_args.args[0])
            self.assertEqual(result['summary'],'Useful <overview>.')
            html=newsletter.render_html('Topic',[result],0,'https://example.org/unsubscribe')
            self.assertIn('Useful &lt;overview&gt;.',html)

    def test_missing_source_does_not_generate_invented_overview(self):
        with patch.object(newsletter,'page_text',side_effect=ValueError),patch.object(newsletter.llm,'ask_json') as ask,self.assertLogs('newsletter',level='ERROR'):
            self.assertEqual(newsletter.overview(post(1))['summary'],'')
            ask.assert_not_called()

    def test_page_extraction_uses_article_and_stops_at_size_limit(self):
        response=Mock();response.is_redirect=False
        response.headers={'Content-Type':'text/html'}
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.iter_content.return_value=iter([
            b'<nav>Noise</nav><article>Useful source<script>bad()</script></article>' + b' '*2_000_000,
            b'Should never be read'])
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]), \
             patch('requests.get',return_value=response):
            self.assertEqual(newsletter.page_text('https://example.com/article'),'Useful source')
        self.assertEqual(next(response.iter_content.return_value),b'Should never be read')

    def test_private_page_addresses_are_rejected_before_request(self):
        with patch('requests.get') as get:
            with self.assertRaises(ValueError):newsletter.page_text('http://127.0.0.1/private')
            get.assert_not_called()

if __name__=='__main__':unittest.main()
