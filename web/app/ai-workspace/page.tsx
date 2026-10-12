'use client';
import Link from 'next/link';
import {useEffect,useState} from 'react';
import {api,getToken} from '@/lib/api';
import NativeAIPage from '@/components/native-ai/NativeAIPage';
export default function Page(){const[ready,setReady]=useState(false);useEffect(()=>{let active=true;if(getToken())api.me().then(()=>{if(active)setReady(true);}).catch(()=>{});return()=>{active=false;};},[]);return ready?<><Link href='/'>Back to operations</Link><NativeAIPage/></>:<main><p>Sign in with your provisioned native identity.</p><Link href='/login'>Sign in</Link></main>;}
